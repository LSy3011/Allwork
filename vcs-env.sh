# Synopsys VCS 环境(Phase 1 验证靶:UVM + co-sim)。
# ★provisioning(2026-07-01,owner sudo):`/bin/sh → bash`(原 dash 不认 vcs wrapper 的
#   `#!/bin/sh -h`;EDA 工具链在 Ubuntu 的标准要求)。此为工具间内部跨调用(如 urg 调 `vcs -location`)
#   的根解——旧 flow/vcs-shim/ 只能拦 PATH 调用、拦不住绝对路径内部调用,故 urg/cm 合并需此系统级修。
#   系统级 sh=bash 后 shim 已冗余、不再入 PATH(flow/vcs-shim/ 保留作历史,可删)。
export VCS_HOME=/home/yq/tools/synopsys/tools/synopsys/vcs/V-2023.12
export PATH="$VCS_HOME/bin:$PATH"
# arch 解析:wrapper 从 uname 得 OS=LiNuX_Dummy→回退 linux,但默认 VCS_MODE_FLAG=0(非64)→
#   VCS_ARCH 停在不存在的 `linux/`(真二进制在 `linux64/`|`amd64/`)。
#   VCS_TARGET_ARCH=linux64 强制 64-bit → VCS_ARCH=linux64,裸 `vcs` 即可 resolve(与 sh=bash 正交,仍需)。
#   (旧值 VCS_ARCH_OVERRIDE=linux 是错的:它喂 OS 名并让 arch 停在 linux/ → BadInstall 假象,
#    致 G23/25/26 曾被误判"真 VCS 未装";脚本里的 -full64 能盖过它,故 UVM 流程一直可跑。)
export VCS_TARGET_ARCH=linux64
# 远程 license 服务器(amd9754 主机名本机解析不了,用 IP)
export LM_LICENSE_FILE=27000@172.16.30.16
export SNPSLMD_LICENSE_FILE=27000@172.16.30.16
