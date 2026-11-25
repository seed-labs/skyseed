import dockerode from 'dockerode';
import { LogProducer } from '../interfaces/log-producer';
import { Logger } from 'tslog';
import { SessionManager, Session } from './session-manager';

export class Sniffer implements LogProducer {
    private _logger: Logger;
    private _listener: (nodeId: string, stdout: any) => void;
    private _capturePacketListener: (nodeId: string) => void;
    private _sessionManager: SessionManager;

    constructor(docker: dockerode) {
        this._logger = new Logger({ name: 'Sniffer' });
        this._sessionManager = new SessionManager(docker, 'Sniffer');
        this._sessionManager.on('new_session', this._listenTo.bind(this));
    }

    private _listenTo(nodeId: string, session: Session) {
        this._logger.debug(`got new session for noed ${nodeId}; attaching listener...`);

        session.stream.addListener('data', data => {
            if (this._listener) {
                this._listener(nodeId, data);
            }
            if (this._capturePacketListener) {
                this._capturePacketListener(nodeId);
            }
        });
    }

    async sniff(nodes: string[], expr: string) {
        this._logger.debug(`sniffing on ${nodes} with expr ${expr}...`);

        var sessions = await Promise.all(nodes.map(node => this._sessionManager.getSession(node, ["bash", "-c", "last_pid=0; while read -sr expr; do [ \"$last_pid\" != 0 ] && kill \"$last_pid\" 2>/dev/null; [ -z \"$expr\" ] && continue; if [ \"$expr\" = all ]; then tcpdump -l -e -i any -nn -p & else tcpdump -l -e -i any -nn -p \"$expr\" & fi; last_pid=$!; done; [ \"$last_pid\" != 0 ] && kill \"$last_pid\""])));

        sessions.forEach(session => {
            try {
                session.stream.write(`${expr}\r`);
            } catch (e) {
                this._logger.error("error communicating with node.");
            }
        });
    }

    setListener(listener: (nodeId: string, stdout: any) => void) {
        this._listener = listener;
    }

    setCapturePacketListener(listener: (nodeId: string) => void) {
        this._capturePacketListener = listener;
    }

    clearListener() {
        this._listener = undefined;
    }

    getLoggers(): Logger[] {
        return [this._logger, this._sessionManager.getLoggers()[0]];
    }
}