# BPF 后端

负责原生 tc-BPF 源码、map 编译/加载、常驻挂载生命周期、数据包证据和归属内清理。严格 `bpf_owned` G/Z 路径禁止回退到 Linux。
