# Emulator

[English](README.md)

SkySEED 构建类与普通 SEED Layer、Service 和 Compiler 一起使用。
已有 [Scene](../scene/README.zh-CN.md) 时：

```python
from seedemu import Routing, Ospf
from skyseed.emulator import SkyEmulator, SkyBase

emulator = SkyEmulator()
base = SkyBase()
routing = Routing()
ospf = Ospf()
ospf.maskAsn(150)

satellite_as = base.createSkyAutonomousSystem(150)
satellite_as.loadScene(scene)
ground_as = base.createAutonomousSystem(151)

emulator.addLayer(base)
emulator.addLayer(routing)
emulator.addLayer(ospf)
```

Layer 显式添加。render 前按 SEED Routing 要求将 GS 路由器接到网络或 IX；
[完整构建例子](../../../examples/tiny_network/emulator.py)包含这些连接。

## 加载与定制

`loadScene(scene)` 创建全部场景节点：卫星为 ExtensionNode，GS 为普通 SEED Router。
`routingBackend="bird"`（默认）或 `"frr"` 选择 GS 软件，与计划转发后端分别配置。

只加载选定节点时，用以下调用替代上面的全部加载：

```python
selected = scene.get_node_ids(groups=["demo", "ground"])
satellite_as.loadScene(scene, node_ids=selected)
```

一个 Scene 可供多个 AS 使用，一个 AS 也可加载互不重叠的场景输入。
组装后的 Emulator 内 node ID 必须唯一。加载须在 SEED 节点注册前完成，不改动 Scene；
随后编辑 Scene 不会同步修改已创建节点。

`getNode(scene_id)` 返回可编辑的 SEED 节点，SkyAS 为局部查询，SkyEmulator 为全局查询；
`getSceneNodes()` 返回包含同一批对象的字典。未知 ID 抛出 `KeyError`，歧义身份会被拒绝。
无需自行拼接生成的 SEED 名称；保留标签 `skyseed.scene_id`／`skyseed.scene_kind`
将身份传递到部署文件。

普通 Node 的 `addSoftware`、`setFile`、`importFile`、`appendStartCommand` 可直接使用。
`skyseed.emulator.configure_native_software` 可配置 BIRD 文本（`daemon="bird"`）或
含 `zebra` 的 FRR 配置字典（`daemon="frr"`）。协议策略由调用方提供；
仅在 SEED Routing 尚未管理该节点软件时使用此 helper。

## 复用与部署

| 操作 | 对象／产物 |
|---|---|
| `loadScene`、节点配置、添加 Layer | 可编辑的 SEED 构建描述 |
| `dump`／`load` | 保存／恢复未 render 的构建描述 |
| `render()` | 完成 Layer 配置和绑定 |
| `compile(Docker(...), output)` | 输出 Compose、Dockerfile 和节点配置 |
| Docker Compose | 构建镜像，启动节点及静态网络 |

SEED `dump`／`load` 保存、恢复**未 render 的构建描述**，包含 Scene ID 查询关系。
这是用于可信描述的 pickle 格式，不是 `.scene`；加载会替换当前描述。
仅含服务的描述可随后与 Base 合并。

<details>
<summary>合并描述与 Base 的对象共享（展开）</summary>

`merge` 接受显式 SEED merger 策略，返回 SkyEmulator。默认 Base merger 返回普通 Base，
共享选中的 AS 对象，不是深复制，也不保留 SkyBase 工厂。
若需扩展普通 Base，可检查 ASN 冲突后用 `setAutonomousSystem` 添加 SkyAS，
或合并另行构建的 SkyEmulator 描述。

</details>

参见[示例命令](../../../examples/tiny_network/README.zh-CN.md)。

## 文件

| 文件 | 职责 |
|---|---|
| `sky_as.py` | 将 Scene 节点加载进 SEED 节点清单 |
| `sky_base.py` | 为 SEED Base 增加 SkyAS 工厂 |
| `sky_emulator.py` | 保留 SEED 生命周期，提供全局 Scene ID 查询 |
| `scene_identity.py` | SEED 名称、Scene 身份标签与歧义检查 |
| `native_routing.py` | 通过公共 Node API 配置可选协议软件 |
