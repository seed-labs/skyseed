const fs = require('node:fs');
const readline = require('node:readline');
const {Readable} = require('node:stream');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const encode = text => Buffer.from(text).toString('base64');

class JournalError extends Error {}

async function post(endpoint, method, body) {
    const response = await fetch(endpoint + '/v3/' + method, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error('etcd HTTP ' + response.status);
    return response;
}

async function followStore(store, apply, status) {
    const prefix = store.prefix;
    const end = Buffer.from(prefix);
    end[end.length - 1]++;
    const range_end = end.toString('base64');
    let revision = 0, sequence = 0;

    function consume(entries) {
        const batch = [];
        let next = sequence;
        for (const kv of entries) {
            const key = Buffer.from(kv.key, 'base64').toString();
            const id = Number(key.slice(prefix.length));
            if (id < next) continue;
            if (id !== next) throw new JournalError('Missing Runner event ' + next);
            batch.push(JSON.parse(Buffer.from(kv.value, 'base64').toString()));
            next++;
        }
        try {
            if (batch.length) apply(batch);
        } catch (error) { throw new JournalError(error.message); }
        sequence = next;
    }

    while (true) {
        try {
            // Runner keys are append-only. Page from the next sequence on startup
            // or after etcd compaction, then watch after the last range revision.
            if (!revision) {
                let page;
                do {
                    const key = encode(prefix + String(sequence).padStart(9, '0'));
                    const response = await post(store.endpoint, 'kv/range', {key, range_end,
                        limit: 128, sort_order: 'ASCEND', sort_target: 'KEY'});
                    page = await response.json();
                    consume(page.kvs || []);
                } while (page.more);
                revision = Number(page.header.revision) + 1;
            }
            const response = await post(store.endpoint, 'watch', {create_request: {
                key: encode(prefix), range_end, start_revision: String(revision),
            }});
            for await (const line of readline.createInterface({input: Readable.fromWeb(response.body)})) {
                const result = JSON.parse(line).result;
                if (result?.canceled && Number(result.compact_revision)) {
                    revision = 0;
                    break;
                }
                if (!result || result.canceled) throw new Error('etcd watch canceled: ' + line);
                const events = result.events || [];
                if (events.length) {
                    consume(events.map(event => event.kv));
                    revision = Number(events.at(-1).kv.mod_revision) + 1;
                }
                status(null);
            }
            if (revision) throw new Error('etcd watch disconnected');
        } catch (error) {
            // Transport failures are retryable. A malformed journal or rejected
            // projection must remain visible rather than replay a partial batch.
            if (error instanceof JournalError || error instanceof SyntaxError) throw error;
            status(error.message);
            await sleep(1000);
        }
    }
}

async function followFile(file, apply, status) {
    const fd = await fs.promises.open(file, 'r');
    let offset = 0, partial = '';
    const buffer = Buffer.alloc(256 * 1024);
    try {
        for (;;) {
            const {bytesRead} = await fd.read(buffer, 0, buffer.length, offset);
            if (!bytesRead) { await sleep(200); continue; }
            offset += bytesRead;
            const lines = (partial + buffer.toString('utf8', 0, bytesRead)).split('\n');
            partial = lines.pop();
            apply(lines.map(line => JSON.parse(line)));
            status(null);
        }
    } finally {
        await fd.close();
    }
}
module.exports = {followStore, followFile};
