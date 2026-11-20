# 本机正演配置

离线打开六份随包结果只需要安装根目录 requirements.txt 中的依赖。

在本机从独立的 SPECFEM2D 8.1.0 源码副本构建。与本次 Mac 配置一致的步骤为：应用 `patch -p1 < /path/to/project/docs/release/upstream/alpha_override.patch`，然后运行 `python /path/to/project/scripts/patch_dual_dump.py .`，执行 `./configure --without-mpi --enable-double-precision FC=gfortran CC=cc` 和 `make -j4 xmeshfem2D xspecfem2D`。核对 NGLLX=5、双精度和本机动态库。仅为具备对应功能的构建填写各能力项；旧服务器二进制的哈希不能用于本机程序。Windows 原生构建尚未实机验证。

从仓库根目录复制 local_compute.example.json 为 local_compute.json，填写本机 bin_dir、可写的 run_parent 与两个二进制的实际 SHA256，各能力项只填写已经核对的构建。CPML 频移配置适用于对应补丁构建；六份三层示例使用 Stacey。

可选理论接口 QEDisp 和 SASEM 需另行配置匹配版本，弹性一维参考可用 disba。
