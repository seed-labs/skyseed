# 仓库工具

[English](README.md)

- [可视化](observation/README.zh-CN.md)：读取保存的 Scene、逻辑计划、编译指令和实际执行记录，不运行网络。
- [S4 view.py](../examples/s4_handover/view.py)：独立导出 Scene／Physical／Link／Network／Compiler 页面的简短用法。
- `deployment/build_images.py`：读取 SEED 部署文件，复用相同内容构建镜像并写运行用 Compose；调用见 S4 Emu 入口。
- [Shell 快捷命令](deployment/shell.sh)：SEED 风格的 Docker 命令、环境入口和 S4 运行查询；配置见下方。

Tools 是可选仓库工具，与安装的 `skyseed` 包分开。

## 快捷命令

完成[安装](../README.zh-CN.md#安装)后，在仓库根目录的 Bash 中加载脚本。
它根据自身路径定位仓库：

```bash
source tools/deployment/shell.sh
skyseed
```

| 命令 | 用途 |
|---|---|
| `skyseed` | 激活安装脚本创建的 `skyseed_dev` Conda 环境，并进入当前仓库 |
| `dockps` | 列出运行中容器的 ID 和名称 |
| `docksh 容器名` | 进入指定容器的 Bash |
| `dcbuild`、`dcup`、`dcdown` | 在当前 Compose 目录执行 Docker Compose build、up、down |
| `skytime`、`skynodes` | 读取 S4 的阶段、轮次、端点与当前服务星 |

每个新终端均需加载脚本。若要长期使用，在 `~/.bashrc` 的 **Conda 初始化之后**加入
`source /你的绝对路径/skyseed/tools/deployment/shell.sh`，将路径替换成自己的仓库。
加载只定义命令，不会在打开终端时自动激活环境或启动容器。
若使用其他名称的环境，自行激活即可，不使用 `skyseed` 入口。

Docker 快捷命令包装的是 SEED 环境中的同类命令，不要求另装 SEED 的 shell 配置。
`skytime`、`skynodes` 需要本机已经启动过 S4；停止后显示最后的记录。
具体用法见[手动实验手册](../examples/s4_handover/MANUAL.zh-CN.md)。
