//=========================================================================
// tb_f6a_lsu_complete.v — F6-A 契约级单元 TB（lsu_complete 完成契约）
//
// 依据：docs/adr/F6-lsu-completion-contract-design.md v3.1（C-BASE/C-N/
// C-DRAIN/C-PAYLOAD/C-ERR；边界①②③；执行令步④「先复现原始竞态」）。
//
// DUT 两形态（-D F6A_LEGACY 选择）：
//   legacy: lsu_complete_legacy_wrap 包装**原版** smi_iq_lsu_complete.v
//           （隔离副本内、未修改），暴露契约端口；tb_outstanding 直驱。
//   fixed : 修复版 smi_iq_lsu_complete（隔离副本内实现后才可编译）。
//
// TB 侧模型纪律：cru/crd/err_sticky 由 TB 按 dly_queen :184-202 语义
// **逐拍注册**（信用脉冲拍 T → T+1 可见；错误与计数同 posedge 共注册，
// R-DELAY-0）；outstanding 按 :174 饱和视图建模（仅供 legacy DUT）。
// TB 只打印 [F6A_FACT] 事实行；契约断言在 f6a_unit_check.py（裁决器
// 与 TB 分离，同 gate3 纪律）。
//
// 可见拍打印约定：脉冲于 negedge（拍 c+1）驱动 → posedge 注册更新 →
// 监测器在拍 c+2 的 negedge 记录 ⇒ 打印的 last_credit_visible = lc+2
// （lc=驱动前记录拍）。done_cycle 同为「监测记录拍」。精确钉拍（==）
// 只约束 fixed 模式；legacy 基线按 >= 判「不早于真实完成」。
//=========================================================================
`timescale 1ns/1ps

module tb_f6a_lsu_complete;

    localparam WIN = 80;   // 每用例观测窗（拍）

    reg         clk = 1'b0;
    reg         rst_n;
    always #5 clk = ~clk;

    integer     cyc = 0;
    always @(negedge clk) cyc = cyc + 1;

    // ---- 契约接口信号（TB 驱动） ----
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

    // ---- TB 事件标志（当拍脉冲，模型 dly_queen 注册语义） ----
    reg         ev_issue;
    reg         ev_credit;
    reg         ev_credit_err;

    // dly_queen :184-202 语义模型：计数与错误同 posedge 共注册
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
    assign err_now = ev_credit & ev_credit_err;   // 当拍组合检出（E1'）

    // outstanding 饱和视图模型（:174：(cnt==4)?3:cnt[1:0]），供 legacy
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

    // ---- DUT ----
    // proto_err（2026-10-10 缺口修复轮，用户复核裁定 #4a）：F6 模块新增
    // 协议异常上报端口（arm_err → proto_err_o）；legacy 包装无此端口，
    // 常 0（C7 的 legacy 判据不经 proto 字段，维持原 VIOLATION 推导）。
    wire proto_err;
`ifdef F6A_LEGACY
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
    assign proto_err = 1'b0;
`else
    // 修复版（F6-A 阶段一）：独立模块名，原 smi_iq_lsu_complete 保持冻结
    // （IQ92 与 legacy 回归继续逐字节引用原版）
    smi_iq_lsu_complete_f6 u_dut (
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
        .proto_err_o          (proto_err),
        .rdi_done_out         (done)
    );
`endif

    // ---- 事实监测（negedge+#1 采样当拍稳定值） ----
    integer done_cycle;
    integer err_seen_cycle;
    integer proto_seen_cycle;
    initial begin done_cycle = -1; err_seen_cycle = -1; proto_seen_cycle = -1; end
    always @(negedge clk) begin
        #1;
        if (done && done_cycle < 0)       done_cycle = cyc;
        if (err_sticky && err_seen_cycle < 0) err_seen_cycle = cyc;
        if (proto_err && proto_seen_cycle < 0) proto_seen_cycle = cyc;
    end

    // ---- 驱动原语（均当拍脉冲） ----
    task t_idle(input integer k); integer i; begin
        for (i = 0; i < k; i = i + 1) @(negedge clk);
    end endtask

    task t_start; begin
        @(negedge clk); start_pulse <= 1'b1;
        @(negedge clk); start_pulse <= 1'b0;
    end endtask

    task t_issue; begin   // 一条指令发射（cru 下一拍可见）
        @(negedge clk); ev_issue <= 1'b1;
        @(negedge clk); ev_issue <= 1'b0;
    end endtask

    task t_credit; begin  // 一笔信用返回（crd 下一拍可见）
        @(negedge clk); ev_credit <= 1'b1;
        @(negedge clk); ev_credit <= 1'b0;
    end endtask

    task t_credit_err; begin  // 携带错误的信用（同拍错误）
        @(negedge clk); ev_credit <= 1'b1; ev_credit_err <= 1'b1;
        @(negedge clk); ev_credit <= 1'b0; ev_credit_err <= 1'b0;
    end endtask

    integer last_dispatch_valid;   // 最近一次 dispatch 载荷的 valid 拍（记录约定）

    task t_dispatch(input [1:0] sid, input [7:0] n); begin
        @(negedge clk);
        last_dispatch_valid = cyc;   // valid 于本拍生效
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
        // 捕获器在沉降窗**之后**清零：err_sticky/done 是注册信号，
        // 在 rst_n=0 后的首个 posedge 才清零；若在入口清零，监测器
        // 会在沉降窗内把上一用例的滞留值重新捕获进来（C5→C6 实测泄漏）。
        done_cycle = -1; err_seen_cycle = -1; proto_seen_cycle = -1;
    end endtask

    task t_watch(input integer w); begin  // 观测窗内静默等待
        t_idle(w);
    end endtask

    // ---- 用例 ----
    // C1 原始竞态复现：dispatch 关闭时 outstanding 恰为 0（末指令未发射），
    //    其后仍有发射与信用。契约：done 不得早于末信用可见+1。
    task case_c1; integer c0, dc, lc; begin
        $display("[F6A_FACT] case=C1 begin");
        c0 = cyc;
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
        t_watch(10);
        $display("[F6A_FACT] case=C1 n=3 dispatch=%0d last_credit_pulse=%0d last_credit_visible=%0d done=%0d err=%0d end",
                 last_dispatch_valid, lc, lc+2, done_cycle, err_seen_cycle);
    end endtask

    // C2 武装前信用已返回（部分+全部两子形态合并：3 笔中 2 笔先于 dispatch）
    task case_c2; integer dc, lc; begin
        $display("[F6A_FACT] case=C2 begin");
        t_start;
        t_issue; t_issue; t_issue;   // 快速发完 3 条
        t_credit; t_credit;          // 2 笔信用先于 dispatch 返回
        dc = cyc;
        t_dispatch(2'd0, 8'd3);
        t_idle(2);
        lc = cyc;
        t_credit;                    // 末笔信用
        t_watch(10);
        $display("[F6A_FACT] case=C2 n=3 dispatch=%0d last_credit_pulse=%0d last_credit_visible=%0d done=%0d err=%0d end",
                 last_dispatch_valid, lc, lc+2, done_cycle, err_seen_cycle);
    end endtask

    // C3 空批：N=0，契约=载荷捕获后首个求值拍完成
    //    （done == dispatch_valid 拍 + 2；dispatch_valid = dc+1）
    task case_c3; integer dc; begin
        $display("[F6A_FACT] case=C3 begin");
        t_start;
        dc = cyc;                    // 与其他用例同口径：valid 拍 = dc+1
        t_dispatch(2'd2, 8'd0);
        t_watch(8);
        $display("[F6A_FACT] case=C3 n=0 dispatch=%0d done=%0d err=%0d end",
                 last_dispatch_valid, done_cycle, err_seen_cycle);
    end endtask

    // C4 末笔信用：dispatch 在所有发射之后（无竞态形态），
    //    done 恰 = 末信用可见+1
    task case_c4; integer dc, lc; begin
        $display("[F6A_FACT] case=C4 begin");
        t_start;
        t_issue; t_issue;
        t_credit;
        t_idle(1);
        dc = cyc;
        t_dispatch(2'd1, 8'd2);
        t_idle(3);
        lc = cyc;
        t_credit;                    // 末笔
        t_watch(10);
        $display("[F6A_FACT] case=C4 n=2 dispatch=%0d last_credit_pulse=%0d last_credit_visible=%0d done=%0d err=%0d end",
                 last_dispatch_valid, lc, lc+2, done_cycle, err_seen_cycle);
    end endtask

    // C5 末笔信用与错误同拍：done 永不出现（窗内），err_sticky 可见
    task case_c5; integer dc, lc; begin
        $display("[F6A_FACT] case=C5 begin");
        t_start;
        t_issue; t_issue;
        t_credit;
        dc = cyc;
        t_dispatch(2'd1, 8'd2);
        t_idle(2);
        lc = cyc;
        t_credit_err;                // 末笔信用携带错误（同拍）
        t_watch(20);
        $display("[F6A_FACT] case=C5 n=2 dispatch=%0d last_credit_pulse=%0d last_credit_visible=%0d done=%0d err=%0d end",
                 last_dispatch_valid, lc, lc+2, done_cycle, err_seen_cycle);
    end endtask

    // C6 连续不同 sid 批：批1（sid=1,N=1）完成后批2（sid=2,N=2），
    //    批2 基线不得被批1 残留污染
    task case_c6; integer d1, l1, d2, l2, dn1; begin
        $display("[F6A_FACT] case=C6 begin");
        t_start;
        t_issue;
        t_dispatch(2'd1, 8'd1);
        d1 = last_dispatch_valid;
        l1 = cyc;
        t_credit;
        t_watch(4);
        dn1 = done_cycle;
        done_cycle = -1;             // 复位捕获器以记录批2 的 done
        t_start;                     // 批2（新基线快照；批1 已结清 crd==cru）
        t_issue; t_issue;
        t_dispatch(2'd2, 8'd2);
        d2 = last_dispatch_valid;
        t_credit;
        l2 = cyc;
        t_credit;
        t_watch(8);
        $display("[F6A_FACT] case=C6 n1=1 n2=2 dispatch1=%0d last_credit1_visible=%0d done1=%0d dispatch2=%0d last_credit2_visible=%0d done2=%0d err=%0d end",
                 d1, l1+2, dn1, d2, l2+2, done_cycle, err_seen_cycle);
    end endtask

    // C7 重复载荷恰逢计数达标（2026-10-10 补丁定向测试）：
    //    N=1 批，末信用使计数达标；重复载荷窗自末信用撤离拍起连开 3 拍，
    //    覆盖「完成判定首次通过的 posedge」的两种口径可能（lcv+1/lcv+2），
    //    保证对打补丁前 DUT 必然触发「同拍记异常又发 done」漏洞（测试有
    //    牙齿，变异核验已实证）。契约：异常优先 ⇒ done1 永不出现；
    //    随后恢复批（新 start 重新武装）须正常完成（不误锁死）。
    task case_c7; integer lc, dn1, lc2; begin
        $display("[F6A_FACT] case=C7 begin");
        t_start;
        t_issue;                     // cru=1
        t_dispatch(2'd1, 8'd1);      // 载荷 N=1 捕获
        lc = cyc;
        @(negedge clk); ev_credit <= 1'b1;                 // 末信用（lc+1 驱动）
        @(negedge clk); ev_credit <= 1'b0;                 // lc+2 撤信用，
        dispatch_closed_valid <= 1'b1;                     //   同窗开重复载荷
        batch_sid <= 2'd3; batch_n <= 8'd7;                //   （垃圾 sid/N）
        @(negedge clk);                                    // lc+3
        @(negedge clk);                                    // lc+4
        @(negedge clk); dispatch_closed_valid <= 1'b0;     // lc+5 撤载荷
        t_watch(10);
        dn1 = done_cycle;
        done_cycle = -1;             // 复位捕获器以记录恢复批的 done
        // 恢复批正控：新 start 重新武装（base: cru==crd==1 无违例），
        // 须按正常钉拍完成，证明补丁不把模块永久锁死
        t_start;
        t_issue;
        t_dispatch(2'd2, 8'd1);
        lc2 = cyc;
        t_credit;
        t_watch(8);
        $display("[F6A_FACT] case=C7 n=1 last_credit_visible=%0d done1=%0d last_credit2_visible=%0d done2=%0d err=%0d proto=%0d end",
                 lc+2, dn1, lc2+2, done_cycle, err_seen_cycle, proto_seen_cycle);
    end endtask

    initial begin
        rst_n = 0; start_pulse = 0; dispatch_closed_valid = 0;
        batch_sid = 0; batch_n = 0;
        ev_issue = 0; ev_credit = 0; ev_credit_err = 0;
        repeat (5) @(negedge clk);
        t_reset;                     // 首个用例前也必须释放复位

        case_c1; t_reset;
        case_c2; t_reset;
        case_c3; t_reset;
        case_c4; t_reset;
        case_c5; t_reset;
        case_c6; t_reset;
        case_c7; t_reset;

        $display("[F6A_FACT] suite=done");
        $finish;
    end

    // 全局看门狗
    initial begin
        #(WIN * 20 * 10);
        $display("[F6A_FACT] suite=timeout");
        $fatal(1, "F6A unit TB watchdog");
    end

endmodule
