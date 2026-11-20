"""Copy the installed SEED tool and add the example's small integration hooks."""
import json
from pathlib import Path
import shutil
import subprocess

import seedemu

from .runtime import IMAGE


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"InternetMap integration point changed: {path}: {old!r}")
    path.write_text(text.replace(old, new))


def save_positions(output):
    # save_context writes positions first. Decode that field alone; the S4
    # physical/route observations following it are hundreds of MB.
    with (output / "observations/plans.json").open() as stream:
        buffer = stream.read(4096)
        marker = '"positions": '
        start = buffer.index(marker) + len(marker)
        buffer = buffer[start:]
        while True:
            try:
                positions, _ = json.JSONDecoder().raw_decode(buffer)
                break
            except json.JSONDecodeError:
                chunk = stream.read(1 << 20)
                if not chunk:
                    raise
                buffer += chunk
    (output / "observations/map-positions.json").write_text(json.dumps(positions))


def prepare(output, source=None):
    output = Path(output)
    source = Path(source) if source else Path(seedemu.__file__).resolve().parents[1] / "tools/InternetMap2"
    if not source.is_dir():
        raise FileNotFoundError(
            f"SEED InternetMap2 source is missing: {source}. "
            "Install the SEED checkout/revision listed in the repository README; "
            "the seedemu package alone does not include this tool."
        )
    destination = output / "internet-map2"
    # Re-copy only maintained upstream files; preserve the local npm build cache.
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("node_modules", "bin", "dist", ".git"))
    here = Path(__file__).parent
    shutil.copy(here / "frontend.mjs", destination / "frontend/src/skyseed.mjs")
    shutil.copy(here / "capture.mjs", destination / "frontend/src/capture.mjs")
    (destination / "frontend/src/skyseed.d.mts").write_text(
        "export function start(ui: unknown, source: unknown, config: unknown): Promise<() => void>;\n")
    shutil.copy(here / "backend.js", destination / "backend/skyseed.js")
    # Native search/capture highlighting must preserve the compact orbit nodes.
    map_ui = destination / "frontend/src/utils/map-ui.ts"
    replace(map_ui, "const staticDefault = {borderWidth: 1, size: 25}",
            "const staticDefault = {borderWidth: 1}")
    replace(map_ui, "const dynamicDefault = {borderWidth: 4, size: 25}",
            "const dynamicDefault = {borderWidth: 4}")
    replace(map_ui, "a.innerText = node.label;", "a.innerText = node.object.meta.emulatorInfo.name || node.label;")
    component = destination / "frontend/src/components/BaseMap/index.vue"
    replace(component, "const inputActiveName = ref('settings')", "const inputActiveName = ref('filter')")
    replace(component, "const dialogVisible = ref(true)", "const dialogVisible = ref(false)")
    replace(component, '<el-tab-pane label="Settings" name="settings"/>', '')
    replace(component, 'ref, onMounted, reactive', 'ref, onMounted, onUnmounted, reactive')
    replace(component, "const props = withDefaults", "import {start as startSkyseed} from '@/skyseed.mjs';\n"
            "let stopSkyseed: (() => void) | undefined;\n"
            "onUnmounted(() => { stopSkyseed?.(); mapUi.value?.stop(); });\n\nconst props = withDefaults")
    replace(component, "if (mapUi.value instanceof MapUi) {\n      await mapUi.value?.start();",
            "if (mapUi.value instanceof MapUi) {\n      stopSkyseed = await startSkyseed(mapUi.value, datasource, config);")
    replace(component, '<el-form-item label="Drag Fixed"', '<el-form-item v-if="false" label="Drag Fixed"')
    replace(component, '<el-tab-pane label="Replay" name="replay">',
            '<el-tab-pane v-if="false" label="Replay" name="replay">')
    replace(component, 'v-model="detailsDialogVisible"\n      width="300"',
            'v-model="detailsDialogVisible"\n      class="skyseed-details"\n      width="480"')
    with component.open("a") as stream:
        stream.write('\n<style>\n.skyseed-details .el-dialog__body { max-height: 68vh; overflow: auto; }\n'
                     '.skyseed-details .el-descriptions__table { table-layout: fixed; width: 100%; }\n'
                     '.skyseed-details .el-descriptions__cell { overflow-wrap: anywhere; }\n'
                     '.skyseed-details .el-descriptions__label:empty { display: none; }\n'
                     '.skyseed-details .skyseed-action { cursor: pointer; color: #409eff; }\n</style>\n')
    replace(destination / "backend/src/main.ts", "app.use('/api/v1', apiV1Router);",
            "require('../skyseed').install(app);\napp.use('/api/v1', apiV1Router);")
    replace(destination / "backend/src/api/v1/main.ts",
            "return _containers.filter(c => c.meta.emulatorInfo.name);",
            "return _containers.filter(c => c.State === 'running' && c.meta.emulatorInfo.name && require('../../../skyseed').includes(c.Id));")
    api = destination / "backend/src/api/v1/main.ts"
    text = api.read_text()
    text = text.replace('docker.listContainers()',
                        "(require('../../../skyseed').inventory() as Promise<dockerode.ContainerInfo[]>)")
    api.write_text(text)
    # Resolve SEED's short console IDs within this run, without listing every container.
    session = destination / "backend/src/utils/session-manager.ts"
    text = session.read_text()
    start = text.index('        var containers = await this._docker.listContainers();')
    end = text.index('        return candidates[0].Id;', start) + len('        return candidates[0].Id;')
    session.write_text(text[:start] + "        return require('../../skyseed').resolve(id);" + text[end:])
    # Empty expression keeps SEED's stop behavior. 'all' captures every packet.
    # Drop -q so the original Log panel includes MPLS labels and inner packets.
    script = ('last_pid=0; while read -sr expr; do '
              '[ "$last_pid" != 0 ] && kill "$last_pid" 2>/dev/null; '
              '[ -z "$expr" ] && continue; '
              'if [ "$expr" = all ]; then tcpdump -l -e -i any -nn -p & '
              'else tcpdump -l -e -i any -nn -p "$expr" & fi; last_pid=$!; done; '
              '[ "$last_pid" != 0 ] && kill "$last_pid"')
    replace(destination / "backend/src/utils/sniffer.ts", "['/seedemu_sniffer']",
            json.dumps(["bash", "-c", script]))
    # Use InternetMap2's own Node 22 / pnpm / Vite build, without old webpack fixes.
    (destination / ".dockerignore").write_text("**/node_modules\n**/bin\n**/dist\n")
    return destination


def build(output):
    destination = prepare(output)
    save_positions(Path(output))
    subprocess.run(["docker", "build", "-t", IMAGE, str(destination)], check=True)
