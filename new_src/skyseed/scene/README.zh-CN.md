# Scene 场景模块

**中文** · [English](README.md)

- `scene.py`：场景所有权、构造、查询与保存读取。
- `scene_entities.py`：星座、shell、轨道、卫星与 GS、位置输入及局部检查。
- `scene_endpoints.py`：稳定接入目的与默认地理划分，不做服务分配或选路。

`Scene.endpoints`、`get_endpoint`、`get_endpoints(kind)` 查询逻辑接入目的，与物理节点分开。
`create_zone` 提供代表点经纬度和可选矩形边界；`create_default_zones` 添加固定的余弦缩放全球划分。
`create_endpoint` 支持其他类型，由模型解释 JSON 数据。身份／类型固定，数据可编辑，保存时检查。
端点保存为 v2 文件的可选 `endpoints` 字段；旧 v2 文件没有该字段时恢复为空集合。

本模块只依赖标准库；算法及 AS 分配由消费者负责。
详见 [Scene 用法与结构](../../../new_docs/scene.md) 和
[场景构造示例](../../../new_examples/tiny_network/scene.py)。
