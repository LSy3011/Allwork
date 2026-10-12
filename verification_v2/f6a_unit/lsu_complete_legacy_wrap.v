//=========================================================================
// lsu_complete_legacy_wrap.v — 旧行为基线包装器（仅 legacy 模式编译）
//
// 内部实例化**原版未修改**的 smi_iq_lsu_complete（隔离副本内，
// sha256=40b4573d…，与 .pre_sync_reset 备份逐字节一致），对外暴露
// F6-A 契约端口（多出的端口忽略，tb_outstanding 为旧模块唯一排空视图）。
// 用途：定向测试的「旧版基线」——竞态用例在其上应 RED（复现原始竞态），
// 兼容用例允许 GREEN（v2 裁决措辞②）。
//=========================================================================
`timescale 1ns/1ps

module lsu_complete_legacy_wrap (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start_pulse,           // 忽略（旧模块无批起点概念）
    input  wire        dispatch_closed_valid,
    input  wire [1:0]  batch_sid,             // 忽略
    input  wire [7:0]  batch_n,               // 忽略
    input  wire [63:0] cru_cnt,               // 忽略
    input  wire [63:0] crd_cnt,               // 忽略
    input  wire        err_sticky,            // 忽略（旧模块无错误排除）
    input  wire        err_now,               // 忽略
    input  wire [1:0]  tb_outstanding,
    output wire        rdi_done_out
);

    smi_iq_lsu_complete u_old (
        .clk                  (clk),
        .rst_n                (rst_n),
        .rdi_done_dispatch    (dispatch_closed_valid),
        .outstanding_inst_num (tb_outstanding),
        .rdi_done_out         (rdi_done_out)
    );

endmodule
