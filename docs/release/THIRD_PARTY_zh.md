# 第三方来源与署名

工作台开发与维护为 Xin Liu。SPECFEM2D 为独立的上游求解器。本仓库提供相关适配补丁，求解器及可选理论后端由使用者另行安装。

|组件|本项目用途|已核查内容/本次范围|
|---|---|---|
|SPECFEM2D 8.1.0|外部谱元正演、DATA模板、CPML频移适配|实际使用源码根LICENSE为GPL v3文本，副本见upstream/SPECFEM2D_LICENSE；补丁及源文件散列保留；不分发二进制或完整求解器|
|PySide6 6.11.0|原生桌面UI|安装元数据为LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only；以依赖安装，不附带wheel或Qt库|
|NumPy 2.4.2|数组|元数据含BSD-3-Clause及其组件附加许可；不捆绑库|
|SciPy 1.17.0|插值/信号处理|实际元数据包含BSD条款；依赖组件各自条款保留，不捆绑库|
|Matplotlib 3.10.8|绘图|实际元数据包含Matplotlib许可协议；不捆绑库|
|disba 0.7.0/CPS|弹性理论|disba安装元数据为BSD 3-Clause；底层来源须继续保留，未将全部CPS版本概括为同一许可|
|QEDisp|可选理论接口|外部依赖，不分发源码/二进制；当前匹配工具构建和其全部许可未在此包中完成独立重建核验|
|SASEM/DispersionCurves|限定VTI理论接口与独立方法参考|外部隔离源码，不分发vendor；匹配实现及许可仍须另行准备，不能把上游名称当成完整授权|

感谢 SPECFEM2D 团队、disba 维护者、Robert B. Herrmann、Pan Lei 和 Caiwang Shi 及合作者。
