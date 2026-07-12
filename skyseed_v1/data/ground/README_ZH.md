# 地面数据

这里保存可复用的 UT、网关、zone、绑定和策略等地理目录。若文件哈希受契约约束，文件名保留基线身份；示例跨星座 shell 复用目录时必须明确说明。

`s5_operational_clean_*` 系列由 `tools/ground_presets/s5_operational_clean/build.py` 生成。覆盖报告把选定的 9 个 UT、5 个网关和 6 个 zone 固化到两小时、10 秒一帧的 S5 分析窗口。S5 不使用 S4 的 UT 策略或绑定目录。
