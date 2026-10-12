//=========================================================================
// tb_f6a_c1_trace.v — 只读核验步③：最小反例 C1 逐拍复核（trace 专用 TB）
//
// 目的：按用户 2026-10-09 只读核验令步③，在隔离副本中用**原始 RTL**
//       重跑 C1，逐拍列出实际发射、信用到达、计数和 done。先确认 TB 的
//       握手、信用含义和采样时序正确（核验结论见文件尾注），再判断 DUT。
//
// 与归档基线的关系：本文件是 artifacts/f6a_unit_legacy_baseline_20261009
//   的**补充取证**，不修改、不替换任何已归档文件（TB/裁决器/运行器/日志
//   全部原样保留）。激励序列与 tb_f6a_lsu_complete.v case_c1 **逐语句
//   一致**（t_start/t_issue/t_credit/t_idle/t_dispatch 原语同语义拷贝），
//   仅增加逐拍 [C1_TRACE] 打印，不改变任何驱动时序。
//
// DUT：lsu_complete_legacy_wrap → 隔离副本内**原版未修改**
//   smi_iq_lsu_complete.v（冻结基线 sha256=40b4573d…149c，R13）。
//
// TB 包装语义核验结论（步③前半，对照真实链）：
//   V1 DUT 真实性：legacy wrap 直实例化冻结 RTL，哈希钉死。✓
//   V2 outstanding 公式：TB :79 与 dly_queen :174 逐字符一致
//      （(cnt[2:0]==3'd4)?2'd3:cnt[1:0]）。✓
//   V3 注册时序：真实 dly_queen 在 posedge 注册 r_executing_inst_cnt
//      （INST_DECODE 发射分支 +1 / 各状态信用 −1 / 同拍保持），
//      outstanding_inst_num 为组合视图；lsu_complete 同 posedge 采样
//      必见**更新前**值。TB 以 negedge 驱动 ev_* + posedge NBA 注册
//      exec_cnt + 组合 tb_outstanding，时序语义逐拍等价。✓
//   V4 同拍发射+信用：真实 RTL 保持（<= r_executing_inst_cnt），
//      TB default 保持，一致。✓
//   V5 dispatch 脉冲：真实 inst_queen WAIT_DONE 态 rdi_done 单拍脉冲；
//      TB dispatch_closed_valid 单拍（negedge→negedge 整一拍）。✓
//   V6 C1 激励可达性：集成壳实证（B1-C 零真信用完成、B3-D done 早于
//      真信用 2 拍、B4 早 13 拍）——C1 形态（dispatch 关闭时
//      outstanding==0、其后仍有发射与信用）是实测形态的**较弱**形式。✓
//   Δ1（诚实注记，对 C1 惰性）：TB 在 exec_cnt==0 时对信用钳位不递减；
//      真实 dly_queen 在 IDLE/WAIT_COND/WAIT_DLY 无条件 −1（0−1→7 即
//      B3 楔死机制）。C1 全程信用≤发射，永不触达该路径，对 C1 结论无影响。
//   Δ2（诚实注记，对 C1 惰性）：TB 的 cru/crd/err 模型仅喂契约端口，
//      legacy wrap 全部忽略；C1 legacy 裁决只依赖 dispatch+outstanding。
//=========================================================================
`timescale 1ns/1ps

module tb_f6a_c1_trace;

    reg         clk = 1'b0;
    reg         rst_n;
    always #5 clk = ~clk;

    integer     cyc = 0;
    always @(negedge clk) cyc = cyc + 1;

    // ---- 契约接口信号（与 tb_f6a_lsu_complete.v 同） ----
    reg         start_pulse;
    reg         dispatch_closed_valid;
    reg  [1:0]  batch_sid;
    reg  [7:0]  batch_n;
    reg  [63:0] cru_cnt;
    reg  [63:0] crd_cnt;
    reg         err_sticky;
    wire        err_now;
    reg  [1:0]  tb_outstanding;
    wire        done;

    reg         ev_issue;
    reg         ev_credit;
    reg         ev_credit_err;

    // dly_queen :184-202 语义模型（与归档 TB 逐句一致）
    always @(posedge clk) begin
        if (!rst_n) begin
            cru_cnt    <= 64'd0;
            crd_cnt    <= 64'd0;
            err_sticky <= 1'b0;
        end else begin
            if (ev_credit) begin
                crd_cnt <= crd_cnt + 64'd1;
                if (ev_credit_err) err_sticky <= 1'b1;
            end
            if (ev_issue) cru_cnt <= cru_cnt + 64'd1;
        end
    end
    assign err_now = ev_credit & ev_credit_err;

    // outstanding 饱和视图模型（:174），与归档 TB 逐句一致
    reg  [2:0] exec_cnt;
    always @(posedge clk) begin
        if (!rst_n) exec_cnt <= 3'd0;
        else case ({ev_issue, ev_credit})
            2'b10:   exec_cnt <= (exec_cnt == 3'd4) ? 3'd4 : exec_cnt + 3'd1;
            2'b01:   exec_cnt <= (exec_cnt == 3'd0) ? 3'd0 : exec_cnt - 3'd1;
            default: exec_cnt <= exec_cnt;
        endcase
    end
    always @(*) tb_outstanding = (exec_cnt == 3'd4) ? 2'd3 : exec_cnt[1:0];

    // ---- DUT（原版 RTL，经 legacy wrap） ----
    lsu_complete_legacy_wrap u_dut (
        .clk                  (clk),
        .rst_n                (rst_n),
        .start_pulse          (start_pulse),
        .dispatch_closed_valid(dispatch_closed_valid),
        .batch_sid            (batch_sid),
        .batch_n              (batch_n),
        .cru_cnt              (cru_cnt),
        .crd_cnt              (crd_cnt),
        .err_sticky           (err_sticky),
        .err_now              (err_now),
        .tb_outstanding       (tb_outstanding),
        .rdi_done_out         (done)
    );

    // ---- 逐拍 trace（negedge+#1 采样当拍稳定值，与归档监测器同口径） ----
    // state 经层级引用取 DUT 内部状态（0=IDLE, 1=WAIT_DRAIN）。
    always @(negedge clk) begin
        #1;
        $display("[C1_TRACE] cyc=%0d rst_n=%b disp=%b issue=%b credit=%b cru=%0d crd=%0d exec=%0d ost=%0d dut_state=%0d done=%b",
                 cyc, rst_n, dispatch_closed_valid, ev_issue, ev_credit,
                 cru_cnt, crd_cnt, exec_cnt, tb_outstanding,
                 u_dut.u_old.state, done);
    end

    // ---- 事实监测（同归档口径，供与基线对照） ----
    integer done_cycle;
    initial done_cycle = -1;
    always @(negedge clk) begin
        #1;
        if (done && done_cycle < 0) done_cycle = cyc;
    end

    // ---- 驱动原语（与归档 TB 逐句一致） ----
    task t_idle(input integer k); integer i; begin
        for (i = 0; i < k; i = i + 1) @(negedge clk);
    end endtask

    task t_start; begin
        @(negedge clk); start_pulse <= 1'b1;
        @(negedge clk); start_pulse <= 1'b0;
    end endtask

    task t_issue; begin
        @(negedge clk); ev_issue <= 1'b1;
        @(negedge clk); ev_issue <= 1'b0;
    end endtask

    task t_credit; begin
        @(negedge clk); ev_credit <= 1'b1;
        @(negedge clk); ev_credit <= 1'b0;
    end endtask

    integer last_dispatch_valid;
    task t_dispatch(input [1:0] sid, input [7:0] n); begin
        @(negedge clk);
        last_dispatch_valid = cyc;
        dispatch_closed_valid <= 1'b1; batch_sid <= sid; batch_n <= n;
        @(negedge clk);
        dispatch_closed_valid <= 1'b0;
    end endtask

    task t_reset; begin
        rst_n = 1'b0;
        start_pulse = 0; dispatch_closed_valid = 0;
        batch_sid = 0; batch_n = 0;
        ev_issue = 0; ev_credit = 0; ev_credit_err = 0;
        repeat (4) @(negedge clk);
        rst_n = 1'b1;
        repeat (2) @(negedge clk);
        done_cycle = -1;
    end endtask

    // ---- C1：原始竞态复现（激励与归档 case_c1 逐语句一致） ----
    integer dc, lc;
    initial begin
        rst_n = 0; start_pulse = 0; dispatch_closed_valid = 0;
        batch_sid = 0; batch_n = 0;
        ev_issue = 0; ev_credit = 0; ev_credit_err = 0;
        repeat (5) @(negedge clk);
        t_reset;

        $display("[C1_TRACE] case=C1 begin");
        t_start;                     // 批起点
        t_issue;                     // 指令1发射
        t_credit;                    // 指令1信用（outstanding 回 0）
        t_idle(1);
        dc = cyc;                    // dispatch 于 outstanding==0 空窗
        t_dispatch(2'd1, 8'd3);      // N=3
        t_issue;                     // 指令2（dispatch 之后才发射）
        t_credit;
        t_issue;                     // 指令3
        t_idle(2);
        lc = cyc;
        t_credit;                    // 指令3信用=末笔（下一拍可见）
        t_idle(10);
        $display("[F6A_FACT] case=C1 n=3 dispatch=%0d last_credit_pulse=%0d last_credit_visible=%0d done=%0d err=%0d end",
                 last_dispatch_valid, lc, lc+2, done_cycle, -1);
        $display("[C1_TRACE] suite=done");
        $finish;
    end

    // 全局看门狗
    initial begin
        #(80 * 20 * 10);
        $display("[C1_TRACE] suite=timeout");
        $fatal(1, "C1 trace TB watchdog");
    end

endmodule
