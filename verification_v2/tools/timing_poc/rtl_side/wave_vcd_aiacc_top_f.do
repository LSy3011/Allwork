# VCS 原生 VPD dump —— 免 Verdi/Novas（方案 C 时序 diff 用）
# UCLI dump 只支持 VPD/EVCD；走 VPD，再用 vpd2vcd 转纯文本 VCD（均 Verdi-free）。
# 用法：simv ... -ucli -do wave_vcd_aiacc_top_f.do
#   后处理：vpd2vcd tb_aiacc_top_f.vpd tb_aiacc_top_f.vcd
dump -file tb_aiacc_top_f.vpd -type VPD
dump -add tb_aiacc_top_f -depth 0
run
