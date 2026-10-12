//=========================================================================
// tb_f6b_smi_top.v — F6-B 子系统级 TB（DUT = 隔离副本内 smi_top 全壳）
//
// 依据：docs/adr/F6-lsu-completion-contract-design.md v3.1（E3/E4、§9.0
// T-FAILLOCK/T-INTFULL/T-RSTLATE；执行令步④后半「FAILED 锁定和通知
// 队列满」）。与 TB-A（f6a_unit）互补：A 测 lsu_complete 单元契约，
// B 测 ctrl_regs glue/通知/恢复在真实集成壳内的行为。
//
// 驱动/观测纪律（执行边界 1）：
//   - host 侧一律走**真实 AXI**（smi_top s_axi 从口，基址 0x8000_0000）；
//     START/状态读取/中断观察/cfg_rst 均为产品 host 协议，不 force 任何
//     内部状态；
//   - 信用注入切在 u_smc 边界端口 i_crd_stb（TB 扮演 SMC 信用源——硬件
//     划分天然切口）。格式 {bit6=err, [5:2]=id, bit1=valid, bit0=0}
//     （legacy/fixed_p1：smi_top 重映射丢 bit6，错误仅经 dly_queen
//     id/计数自检触发；fixed_p2（阶段二）：bit6 经 crd_err_i 链入
//     dly_queen 检出条件，E2 生效，B6 定向）。**每次注入打印
//     [F6B_INJ] 记录队列/id/err/拍号**；
//   - 发射计数观测 o_debug_iq_vld[24]（产品已有调试口）；完成/错误/通知
//     状态一律 AXI 读 0x004/0x024；中断时刻观测 o_intr 电平边沿。
//   TB 只打印事实行；契约断言在 f6b_check.py（分离裁决）。
//
// 用例与**预冻结预期**（执行边界 2；AS-EXPECTED=旧版行为符合预期定义，
// 不等于新契约通过）：
//   B1 T-FAILLOCK：3 指令批 + 好信用(id0) + 错id信用(id5→err) 后断供
//     → legacy: err 通知 intid+1 但 int_err 不置位（R6）、批楔死
//     （outstanding=1）、cfg_rst 后新批 C **会执行**（R4 清锁）；
//     fixed: int_err 置位、FAILED 锁定、cfg_rst 后 C 仍不得执行。
//     两版 rst_n 后 D 均应恢复（恢复控制）。
//   B2 T-INTFULL：260 空批（iq_en=0），host 不消费（intid_sw=0）→
//     intid_hw 爬至 255 停住、int_err 置位、intr 悬挂（R11 既有机制，
//     两版皆应 PASS = 兼容用例）。
//   B3 T-RSTLATE（字面序：在飞→cfg_rst→旧信用→新 START）：
//     legacy 预期=旧信用被空队列吸收（crd 0→1，executing 0→**7 下溢**，
//     dly_queen :220）→ 发射门 7<4 恒假 → C **静默楔死**（零发射零完成
//     零通知）。判据 v1（阶段一）：「无发射/无完成/无通知」字面项两版
//     皆可 PASS（host 面楔死与锁定不可区分）。**判据 v2（2026-10-10，
//     §12.5/§12.6）：「及时检出」标准下静默吸收即违约**——fixed_p2 预期
//     旧信用被 off-by-one 修复检出（crd==cru==0 越界）→ 错误通知一次
//     （intid_p3==1、int_err、failed）+ C 被锁存拒发；legacy/fixed_p1
//     无检出 ⇒ VIOLATION（B3 翻转定性=判据演进非行为变化，归档不动）。
//   B4 T-RSTLATE-ATTRIB（归因探针：在飞→cfg_rst→新 START C→旧信用）：
//     legacy 预期=C 发射（cfg_rst 清锁）后旧信用 id=0 恰合新批计数
//     （crd 0==cru 基线）→ **无真信用完成 C = 假成功**（R10 危害复现，
//     VIOLATION）；记录首发射拍/信用拍/intr 边沿拍以定因果序（若
//     intr 先于信用，则为 IQ24 竞态形态提前完成，同属违约）。
//     fixed_p2：FAILED 锁定 ⇒ C 不发射；旧信用被 off-by-one 修复后的
//     dly_queen 检出（crd==cru==0 越界）→ 错误通知一次（intid+1/
//     int_err/intr），无完成（判据 v2，见 F6 文档 §12.5/§12.6）。
//   B5 T-RSTLATE-ATTRIB2（归因加固：C=2 指令防竞态抢跑 + 两笔旧信用
//     id=0/1 足额凑齐新批计数）：预期与机制见用例内注释（2026-10-09
//     冻结，晚于 B1-B4 首轮实测——B4 显示 1 指令批会被 IQ24 竞态
//     抢先完成，归因需 N≥2 才能与竞态区分）。
//     fixed_p2：同 B4——首笔旧信用检出即通知一次；第二笔粘滞期内不
//     重复自增（「同一失败只通知一次」实证）。
//   B6 T-UNITERR（2026-10-10 判据 v2 新增，E2 定向）：1 指令批 +
//     id 正确但 bit6=1 的信用 → legacy/fixed_p1 丢错误位 ⇒ 假完成
//     （VIOLATION）；fixed_p2 ⇒ 检出/通知一次/锁存/拒完成，rst_n
//     恢复。
//   ===== 判据 v3（2026-10-10，用户复核裁定补三缺口，首跑前冻结） =====
//   B7 T-ERRPRIO（控制层错误同拍优先）：IQ24 好信用与 IQ26 错误位信用
//     背靠背注入（beat 间距 +2 ⇒ 错误可见拍==批 A 完成拍）→ 错误与
//     batch_done 同拍时「不登记成功」（0x024 done 保持 0）+ 通知一次
//     记错误 + FAILED 锁存 + cfg_rst 不解锁 + rst_n 恢复。
//     legacy：N=1 竞态完成远早于信用窗且 idle 首笔信用被 `>` 旧判据
//     静默吸收 ⇒ doneA7=1/无 int_err/无锁存（VIOLATION）；
//     fixed_p1：完成已等信用但控制层无错误排除 ⇒ doneA7=1（VIOLATION）。
//   B8 T-ERRMASK（通知遮蔽）：无批次纯通知路径——IQ25 错误位信用 ×2
//     （同源第二笔无新边沿，「同一失败只通知一次」牙齿）+ IQ26 一笔
//     （另一源持高期间的新源错误，逐源边沿检验）。
//     legacy/fixed_p1：idle 首笔吸收、第二笔 `>` 触发 intid 0→1、
//     IQ26 首笔再吸收 ⇒ intid 终值 1/无 int_err/无锁存（VIOLATION）；
//     fixed_p2b：intid 0→1→1→2、int_err=1、failed=1（PASS）。
//   B9 T-PROTOERR（协议异常上报）：force u_lsu_complete_24.arm_err
//     （诊断性强制，模拟协议异常已发生；异常产生机制本身的定向证据
//     在 TB-A C7 proto 字段）→ lsu_err_evt **独立**进入通知判定
//     （ir_err 全程未参与）⇒ intid+1 ∧ int_err ∧ FAILED；源撤销
//     （release）后锁存保持；rst_n 恢复。仅 fixed_p2 编译
//     （+define+F6B_HAS_F6PROTO），legacy/fixed_p1 打印 skipped=1，
//     裁决矩阵记 S。
//
// 字段勘定（首轮实测校正，TB 侧 bug 非 DUT）：0x024 DONE 字
//   = {22'd0, eid[1:0], 6'd0, done}（ctrl_regs :374，共 31bit，MSB
//   零扩展）→ eid 在 w[8:7] 而非 w[9:8]。
//
// 已知环境观察（执行边界 4，不补绑）：smi_top :289-292 的
// ir_stb/stbaxi/ldb/ldbaxi_rdy 四线声明后无驱动（ir_ready_bus[27:24]=z）。
// 本 TB 复用真实 STB 指令字（gate3 实测可发射），B1 的 iss0==3 子句即
// 其经验核查；若发射为零则单列阻断，不改 RTL。
//=========================================================================
`timescale 1ns/1ps

module tb_f6b_smi_top;

    localparam [31:0] AXI_BASE  = 32'h8000_0000;
    localparam [31:0] IQ24_BASE = 32'h0000_1000;   // + sid*0x1000 + 192/208

    // 真实 STB 指令（m3lite_g4_respacct_20261008_to_s4/v3_raw/smc_0/
    // iq_stb.hex 唯一行，104bit hex 低 100bit 有效）。内容语义在本壳内
    // 不被消费（cru 输出悬空、信用由 TB 注入），仅要求队列为可发射形态。
    localparam [103:0] STB_INST = 104'h0800000000100200020f104001;

    reg clk = 1'b0;
    reg rst_n;
    always #5 clk = ~clk;
    integer cyc = 0;
    always @(negedge clk) cyc = cyc + 1;

    // ---- AXI 从口信号 ----
    reg  [11:0]  awid = 0, arid = 0;
    reg  [31:0]  awaddr = 0, araddr = 0;
    reg  [3:0]   awlen = 0, arlen = 0;
    reg  [2:0]   awsize = 3'b100, arsize = 3'b100;   // 16B
    reg  [1:0]   awburst = 2'b01, arburst = 2'b01;
    reg          awvalid = 0, wvalid = 0, arvalid = 0;
    wire         awready, wready, arready;
    reg  [127:0] wdata = 0;
    reg  [15:0]  wstrb = 0;
    reg          wlast = 0;
    wire [11:0]  bid, rid;
    wire [1:0]   bresp, rresp;
    wire         bvalid, rvalid, rlast;
    reg          bready = 1, rready = 1;
    wire [127:0] rdata;

    // ---- 信用口（u_smc 边界切口） ----
    reg  [128:0] i_crd_shiftdn = 0;
    reg  [6:0]   i_crd_stb = 0, i_crd_stbaxi = 0, i_crd_ldb = 0,
                 i_crd_ldbaxi = 0, i_crd_ds = 0;

    wire         o_cfg_rst, o_intr;
    wire [93:0]  o_debug_iq_vld;

    // ---- DUT：隔离副本内 smi_top（PARAM_SMC_NUM=8 对齐生产 u_smi_top） ----
    smi_top #(.DATA_WIDTH(128), .PARAM_SMC_NUM(8)) u_dut (
        .clk(clk), .rst_n(rst_n),
        .s_axi_awid(awid), .s_axi_awaddr(awaddr), .s_axi_awlen(awlen),
        .s_axi_awsize(awsize), .s_axi_awburst(awburst),
        .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid), .s_axi_wlast(wlast), .s_axi_wready(wready),
        .s_axi_bid(bid), .s_axi_bresp(bresp), .s_axi_bvalid(bvalid),
        .s_axi_bready(bready),
        .s_axi_arid(arid), .s_axi_araddr(araddr), .s_axi_arlen(arlen),
        .s_axi_arsize(arsize), .s_axi_arburst(arburst),
        .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rid(rid), .s_axi_rdata(rdata), .s_axi_rresp(rresp),
        .s_axi_rlast(rlast), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .i_crd_shiftdn(i_crd_shiftdn), .i_crd_stb(i_crd_stb),
        .i_crd_stbaxi(i_crd_stbaxi), .i_crd_ldb(i_crd_ldb),
        .i_crd_ldbaxi(i_crd_ldbaxi), .i_crd_ds(i_crd_ds),
        .o_cru_intadd(), .o_cru_int8tofp16(), .o_cru_fp16mul(),
        .o_cru_fp16mul_s0123(), .o_cru_fp16mul_s4567(),
        .o_cru_fp32addtree5to1(), .o_cru_fp32addtree8to1(),
        .o_cru_fpmul(), .o_cru_fpadd(), .o_cru_fptofp(), .o_cru_inttoint(),
        .o_cru_fptoint(), .o_cru_inttofp(), .o_cru_logic(),
        .o_cru_shiftup(), .o_cru_shiftdn(),
        .o_cru_urrd0(), .o_cru_urrd1(), .o_cru_urrd2(), .o_cru_urrd3(),
        .o_cru_urwr0(), .o_cru_urwr1(), .o_cru_urwr2(), .o_cru_urwr3(),
        .o_cru_stb(), .o_cru_stb_axi(), .o_cru_ldb(), .o_cru_ldb_axi(),
        .o_cru_rtovr(), .o_cru_ds(), .o_cru_vin0(), .o_cru_vin1(),
        .o_cru_vout0(),
        .o_cfg_rst(o_cfg_rst), .o_intr(o_intr),
        .o_debug_iq_vld(o_debug_iq_vld)
    );

    // ---- 监测（negedge+#1 采样；捕获器清零在 hard_reset 沉降窗之后） ----
    integer issue24_cnt = 0;             // 单调计数，窗用差值
    integer intr_cyc;                    // o_intr 首个上升沿拍
    integer first_issue_cyc;             // 窗口内首个 IQ24 发射拍
    reg     cap_issue;                   // 首发射捕获使能（窗口起点置位）
    initial begin intr_cyc = -1; first_issue_cyc = -1; cap_issue = 0; end
    always @(negedge clk) begin
        #1;
        if (o_debug_iq_vld[24]) begin
            issue24_cnt = issue24_cnt + 1;
            if (cap_issue && first_issue_cyc < 0) first_issue_cyc = cyc;
        end
        if (o_intr && intr_cyc < 0) intr_cyc = cyc;
    end

    // ---- posedge 接受计数监测 ----
    // 握手事实一律以 posedge 采样为准：negedge 采样的电平等待会被「半个
    // 周期后沿组合路径抬起的 ready」骗出幻影握手（2026-10-09 实证：
    // DW_axi_gs 的 aselect 在 P1 注册 arvalid 后，arready 组合抬升，
    // negedge 采样误判握手完成并提前撤 arvalid → 读命令从未入 fifo_addr，
    // fdv=0/srv=0 → R 挂死）。写通道此前「能用」只是 aselect 缺省停在
    // AW 分支的相位侥幸。计数器法对 AW/W/B/AR/R 一律稳健。
    integer aw_acc = 0, w_acc = 0, b_acc = 0, ar_acc = 0, r_acc = 0;
    reg [127:0] rdata_hold = 0;
    always @(posedge clk) begin
        if (awvalid && awready) aw_acc <= aw_acc + 1;
        if (wvalid  && wready ) w_acc  <= w_acc + 1;
        if (bvalid  && bready ) b_acc  <= b_acc + 1;
        if (arvalid && arready) ar_acc <= ar_acc + 1;
        if (rvalid  && rready ) begin r_acc <= r_acc + 1; rdata_hold <= rdata; end
    end

    // ---- AXI 单拍写 ----
    task axi_wr(input [31:0] addr, input [127:0] data, input [15:0] strb);
        integer m_aw, m_w, m_b, to;
    begin
        @(negedge clk);
        m_aw = aw_acc; m_w = w_acc; m_b = b_acc;
        awaddr <= addr; awvalid <= 1;
        wdata  <= data; wstrb  <= strb; wvalid <= 1; wlast <= 1;
        to = 0;
        while (aw_acc == m_aw) begin
            @(negedge clk); to = to + 1;
            if (to == 200) $display("[F6B_DBG] axi_wr AW 挂起 addr=%h cyc=%0d", addr, cyc);
        end
        awvalid <= 0;   // 退出拍即撤（NBA），防下一 posedge 重复接受
        to = 0;
        while (w_acc == m_w) begin
            @(negedge clk); to = to + 1;
            if (to == 200) $display("[F6B_DBG] axi_wr W 挂起 addr=%h cyc=%0d", addr, cyc);
        end
        wvalid <= 0; wlast <= 0;
        to = 0;
        while (b_acc == m_b) begin
            @(negedge clk); to = to + 1;
            if (to == 200) $display("[F6B_DBG] axi_wr B 挂起 addr=%h cyc=%0d", addr, cyc);
        end
        @(negedge clk);   // 事务间间隔，保证 cfg_reg_we 边沿检测
    end
    endtask

    // CSR 32bit 写（lane = addr[3:2]，与 ctrl_regs cfg_rdata 放置同规则）
    task csr_wr(input [11:0] addr, input [31:0] data);
        reg [127:0] pack; reg [15:0] sp;
    begin
        pack = {96'd0, data} << (32 * addr[3:2]);
        sp   = 16'h000F     << (4  * addr[3:2]);
        axi_wr(AXI_BASE + {20'd0, addr}, pack, sp);
    end
    endtask

    // CSR 32bit 读（lane 提取；握手以 posedge 计数为准）
    task csr_rd(input [11:0] addr, output [31:0] word);
        integer m_ar, m_r, to;
    begin
        @(negedge clk);
        m_ar = ar_acc; m_r = r_acc;
        // 读通道协议：arsize=2（32bit 字）+ 精确字地址，DW_axi_gs 负责
        // 128bit GIF 转换与返回 lane 放置（AXI 窄带读数据在地址对应的
        // 字节 lane 上）。
        araddr <= AXI_BASE + {20'd0, addr}; arsize <= 3'b010; arvalid <= 1;
        to = 0;
        while (ar_acc == m_ar) begin
            @(negedge clk); to = to + 1;
            if (to == 200) $display("[F6B_DBG] csr_rd AR 挂起 addr=%h arready=%b cyc=%0d",
                                    addr, arready, cyc);
        end
        arvalid <= 0;   // 退出拍即撤，防重复接受
        to = 0;
        while (r_acc == m_r) begin
            @(negedge clk); to = to + 1;
            if (to == 200) $display("[F6B_DBG] csr_rd R 挂起 addr=%h rvalid=%b raw_gif_valid=%b gif_rvld=%b mread=%b smstate=%b srv=%b fdv=%b cyc=%0d",
                                    addr, rvalid, u_dut.raw_gif_valid, u_dut.gif_rvld,
                                    u_dut.u_axi_wrapper.mread,
                                    u_dut.u_axi_wrapper.u_dw_axi_gs.sm.state,
                                    u_dut.u_axi_wrapper.u_dw_axi_gs.req.start_rd_valid,
                                    u_dut.u_axi_wrapper.u_dw_axi_gs.req.faddr_dst_vld, cyc);
        end
        case (addr[3:2])
            2'b00: word = rdata_hold[31:0];
            2'b01: word = rdata_hold[63:32];
            2'b10: word = rdata_hold[95:64];
            default: word = rdata_hold[127:96];
        endcase
        @(negedge clk);
    end
    endtask

    // IQ24 指令写：slot [196,208]，跨 beat 192（bytes 4..15）与 208（byte 0）
    task wr_iq24(input [1:0] sid);
        reg [31:0] base;
    begin
        base = AXI_BASE + IQ24_BASE + (sid * 32'h1000);
        axi_wr(base + 32'd192, {STB_INST[95:0], 32'h0}, 16'hFFF0);
        axi_wr(base + 32'd208, {120'd0, STB_INST[103:96]}, 16'h0001);
    end
    endtask

    // IQ24 信用注入（一 beat 脉冲；逐笔打印记录，执行边界 1）
    task credit24(input [3:0] id, input biterr);
    begin
        @(negedge clk);
        i_crd_stb <= {biterr, id, 1'b1, 1'b0};
        $display("[F6B_INJ] q=24 id=%0d err=%0d cyc=%0d", id, biterr, cyc);
        @(negedge clk);
        i_crd_stb <= 7'd0;
    end
    endtask

    // IQ25（STBAXI）/IQ26（LDB）信用注入（判据 v3 B7/B8 用；位布局与
    // credit24 相同，smi_top 对各 LSU 端口的重映射结构一致）
    task credit25(input [3:0] id, input biterr);
    begin
        @(negedge clk);
        i_crd_stbaxi <= {biterr, id, 1'b1, 1'b0};
        $display("[F6B_INJ] q=25 id=%0d err=%0d cyc=%0d", id, biterr, cyc);
        @(negedge clk);
        i_crd_stbaxi <= 7'd0;
    end
    endtask

    task credit26(input [3:0] id, input biterr);
    begin
        @(negedge clk);
        i_crd_ldb <= {biterr, id, 1'b1, 1'b0};
        $display("[F6B_INJ] q=26 id=%0d err=%0d cyc=%0d", id, biterr, cyc);
        @(negedge clk);
        i_crd_ldb <= 7'd0;
    end
    endtask

    // B7 专用融合注入（判据 v3 首跑后修正）：q24 干净信用与 q26 带错信用
    // 的**接受拍恰差 1 拍**（q26 晚 1 拍）。此前用两 task 背靠背调用，
    // 接受间距实为 +2 拍——fixed_p2b 首跑实测 err 比完成晚 1 拍注册
    // （doneA7=1/intid7=2，非同拍竞态）。设完成信用接受后 m+2 拍注册
    // done（lcv+2 契约时序）、错误信用接受后 m+1 拍注册粘滞 err，则
    // 接受间距 +1 ⇒ 两寄存器在同一 posedge 置位——真正的「错误与完成
    // 同拍」。逐拍对消与绝对延迟 m 无关（间距线性平移）。
    task credit24_26_aligned(input [3:0] id);
    begin
        @(negedge clk);
        i_crd_stb <= {1'b0, id, 1'b1, 1'b0};
        $display("[F6B_INJ] q=24 id=%0d err=0 cyc=%0d (aligned head)", id, cyc);
        @(negedge clk);
        i_crd_stb <= 7'd0;
        i_crd_ldb <= {1'b1, id, 1'b1, 1'b0};
        $display("[F6B_INJ] q=26 id=%0d err=1 cyc=%0d (aligned tail)", id, cyc);
        @(negedge clk);
        i_crd_ldb <= 7'd0;
    end
    endtask

    task t_idle(input integer k); integer i;
    begin for (i = 0; i < k; i = i + 1) @(negedge clk); end
    endtask

    task host_cfg; begin                 // iq_en=IQ24, cfg_en=1
        csr_wr(12'h010, 32'h0100_0000);
        csr_wr(12'h000, 32'h0000_0001);
    end endtask

    task host_cfg_noiq; begin            // iq_en=0（空批）, cfg_en=1
        csr_wr(12'h010, 32'h0000_0000);
        csr_wr(12'h000, 32'h0000_0001);
    end endtask

    task hard_reset; begin
        rst_n = 0;
        awvalid = 0; wvalid = 0; wlast = 0; arvalid = 0;
        i_crd_stb = 0; i_crd_stbaxi = 0; i_crd_ldb = 0;
        i_crd_ldbaxi = 0; i_crd_ds = 0; i_crd_shiftdn = 0;
        t_idle(6);
        rst_n = 1;
        t_idle(6);
        // 捕获器在沉降窗之后清零（TB-A 教训：沉降窗内注册信号未清，
        // 监测器会重捕获滞留值）
        intr_cyc = -1; first_issue_cyc = -1; cap_issue = 0;
    end endtask

    // ================= 用例变量 =================
    integer iss0, iss_m, iss_blk, iss_postrst, iss_p2;
    integer intid_err, interr_err, doneB;
    integer doneC, eidC, intid_prerstn, doneD, eidD, intid_final;
    integer failed_err, failed_postrst, failed_postrstn;   // 判据 v2：E4 可观测
    integer intid_mid, interr_full, intr_full;
    integer doneC3, eidC3, intid_p3, doneD3, eidD3, interr_p3, failed_p3;
    integer doneC4, eidC4, intid_p4, doneD4, eidD4, late_cyc, intr_cyc4;
    integer interr_p4, failed_p4;
    integer first_issue_cyc4, first_issue_cyc5;
    integer doneC5, eidC5, intid_p5, doneD5, eidD5, late1_cyc, intr_cyc5;
    integer interr_p5, failed_p5;
    integer iss6, intid6, interr6, failed6, doneA6, iss_p6, doneC6;
    integer failed6b, doneD6, eidD6;
    integer iss7, intid7, interr7, failed7, doneA7, iss_p7, doneC7;
    integer failed7b, doneD7, eidD7;                     // B7 T-ERRPRIO
    integer intid8a, interr8a, failed8a, intid8b, intid8c, interr8c, failed8c;
    integer doneD8;                                      // B8 T-ERRMASK
    integer intid9, interr9, failed9, iss_p9, doneC9, failed9b, doneD9, eidD9;
    reg [31:0] w;
    integer i;

    initial begin
        rst_n = 0;
        hard_reset;

        // ================= B1 T-FAILLOCK =================
        $display("[F6B_FACT] case=B1 begin");
        host_cfg;
        wr_iq24(2'd0); wr_iq24(2'd0); wr_iq24(2'd0);      // 批 A：3 指令 sid0
        csr_wr(12'h020, 32'h0000_0001);                   // START sid0
        t_idle(12);
        iss0 = issue24_cnt;                               // 健全性：期望 3
        credit24(4'd0, 1'b0); t_idle(4);                  // 好信用
        credit24(4'd5, 1'b0); t_idle(8);                  // 错 id → dly err
        csr_rd(12'h004, w);
        intid_err  = w[15:8];                             // 错误事件已通知？
        interr_err = w[1];                                // int_err？（fixed=1）
        failed_err = w[2];                                // FAILED 锁存？（判据 v2）
        wr_iq24(2'd1);                                    // 批 B 指令 sid1
        csr_wr(12'h020, 32'h0000_0101);                   // START B（排队/被拒）
        iss_m = issue24_cnt; t_idle(40);
        iss_blk = issue24_cnt - iss_m;                    // 期望 0
        csr_rd(12'h024, w); doneB = w[0];
        csr_wr(12'h000, 32'h0000_0002); t_idle(8);        // cfg_rst
        host_cfg;
        wr_iq24(2'd2);                                    // 批 C 指令 sid2
        csr_wr(12'h020, 32'h0000_0201);                   // START C
        iss_m = issue24_cnt; t_idle(40);
        iss_postrst = issue24_cnt - iss_m;                // legacy>0/fixed==0
        csr_rd(12'h024, w); doneC = w[0]; eidC = w[8:7];
        csr_rd(12'h004, w); intid_prerstn = w[15:8];
        failed_postrst = w[2];                            // 锁存挺过 cfg_rst？（E4 核心证据）
        hard_reset;                                       // rst_n 唯一解锁
        host_cfg;
        wr_iq24(2'd3);                                    // 批 D sid3（恢复控制）
        csr_wr(12'h020, 32'h0000_0301);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD = w[0]; eidD = w[8:7];
        csr_rd(12'h004, w); intid_final = w[15:8];
        failed_postrstn = w[2];                           // rst_n 已解锁？（恢复正面证据）
        $display("[F6B_FACT] case=B1 iss0=%0d intid_err=%0d interr_err=%0d failed_err=%0d iss_blk=%0d doneB=%0d iss_postcfgrst=%0d doneC=%0d eidC=%0d intid_prerstn=%0d failed_postrst=%0d doneD=%0d eidD=%0d intid_final=%0d failed_postrstn=%0d end",
                 iss0, intid_err, interr_err, failed_err, iss_blk, doneB, iss_postrst,
                 doneC, eidC, intid_prerstn, failed_postrst, doneD, eidD, intid_final,
                 failed_postrstn);
        hard_reset;

        // ================= B2 T-INTFULL =================
        $display("[F6B_FACT] case=B2 begin");
        host_cfg_noiq;
        intid_mid = -1;
        for (i = 0; i < 260; i = i + 1) begin
            csr_wr(12'h020, ((i % 4) << 8) | 32'h1);
            t_idle(4);
            if (i == 49) begin
                t_idle(16);
                csr_rd(12'h004, w); intid_mid = w[15:8];
            end
        end
        t_idle(20);
        csr_rd(12'h004, w);
        intid_final = w[15:8]; interr_full = w[1]; intr_full = w[0];
        $display("[F6B_FACT] case=B2 intid_mid50=%0d intid_final=%0d interr=%0d intr=%0d end",
                 intid_mid, intid_final, interr_full, intr_full);
        hard_reset;

        // ============ B3 T-RSTLATE（字面序：旧信用先于新 START） ============
        $display("[F6B_FACT] case=B3 begin");
        host_cfg;
        wr_iq24(2'd0); wr_iq24(2'd0);                     // 批 A：2 指令 sid0
        csr_wr(12'h020, 32'h0000_0001);
        t_idle(10);
        credit24(4'd0, 1'b0); t_idle(4);                  // 1/2 信用，批在飞
        csr_wr(12'h000, 32'h0000_0002); t_idle(8);        // 在飞 cfg_rst
        host_cfg;
        credit24(4'd0, 1'b0); t_idle(6);                  // 旧信用先到达
        wr_iq24(2'd2);                                    // 新批 C sid2
        csr_wr(12'h020, 32'h0000_0201);
        iss_m = issue24_cnt; t_idle(30);
        iss_p2 = issue24_cnt - iss_m;
        csr_rd(12'h024, w); doneC3 = w[0]; eidC3 = w[8:7];
        csr_rd(12'h004, w); intid_p3 = w[15:8];
        interr_p3 = w[1]; failed_p3 = w[2];               // 判据 v2：检出上报 + 锁存
        hard_reset;                                       // rst_n 初始化对照
        host_cfg;
        wr_iq24(2'd1);                                    // 批 D sid1
        csr_wr(12'h020, 32'h0000_0101);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD3 = w[0]; eidD3 = w[8:7];
        $display("[F6B_FACT] case=B3 iss_p2=%0d doneC=%0d eidC=%0d intid_p3=%0d interr_p3=%0d failed_p3=%0d intr_cyc=%0d doneD=%0d eidD=%0d end",
                 iss_p2, doneC3, eidC3, intid_p3, interr_p3, failed_p3, intr_cyc, doneD3, eidD3);
        hard_reset;

        // ====== B4 T-RSTLATE-ATTRIB（归因探针：旧信用在新批发射后到达） ======
        $display("[F6B_FACT] case=B4 begin");
        host_cfg;
        wr_iq24(2'd0); wr_iq24(2'd0);                     // 批 A：2 指令 sid0
        csr_wr(12'h020, 32'h0000_0001);
        t_idle(10);
        credit24(4'd0, 1'b0); t_idle(4);                  // 1/2 信用，批在飞
        csr_wr(12'h000, 32'h0000_0002); t_idle(8);        // 在飞 cfg_rst
        host_cfg;
        wr_iq24(2'd2);                                    // 新批 C sid2（1 指令）
        csr_wr(12'h020, 32'h0000_0201);
        cap_issue = 1;
        iss_m = issue24_cnt; t_idle(20);
        iss_p2 = issue24_cnt - iss_m; cap_issue = 0;
        late_cyc = cyc;
        credit24(4'd0, 1'b0); t_idle(15);                 // 旧信用此时到达
        intr_cyc4 = intr_cyc;
        first_issue_cyc4 = first_issue_cyc;               // 须在 hard_reset 前取存
        csr_rd(12'h024, w); doneC4 = w[0]; eidC4 = w[8:7];
        csr_rd(12'h004, w); intid_p4 = w[15:8];
        interr_p4 = w[1]; failed_p4 = w[2];               // 判据 v2
        hard_reset;
        host_cfg;
        wr_iq24(2'd1);                                    // 批 D sid1（恢复控制）
        csr_wr(12'h020, 32'h0000_0101);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD4 = w[0]; eidD4 = w[8:7];
        $display("[F6B_FACT] case=B4 iss_p2=%0d first_issue_cyc=%0d late_cyc=%0d intr_cyc=%0d doneC=%0d eidC=%0d intid_p4=%0d interr_p4=%0d failed_p4=%0d doneD=%0d eidD=%0d end",
                 iss_p2, first_issue_cyc4, late_cyc, intr_cyc4,
                 doneC4, eidC4, intid_p4, interr_p4, failed_p4, doneD4, eidD4);
        hard_reset;

        // ====== B5 T-RSTLATE-ATTRIB2（归因探针加固版：C=2 指令防竞态抢跑） ======
        // 2026-10-09 冻结预期（先于首次运行）：
        //   legacy：C 双发射后，两笔旧信用 id=0/1 恰合新批计数基线
        //     （crd 0→1→2 == cru 2，id 逐笔匹配、预增比较不越界 → 无 err）
        //     → executing 2→1→0 → outstanding 0 → **无真信用完成 C**
        //     （真·归因假成功，R10 危害直接复现）→ VIOLATION；
        //     intr 边沿应落在第二笔旧信用之后（与 B4 的竞态抢跑区分）。
        //   fixed：FAILED 锁定 ⇒ C 不发射、无完成无通知。
        //   两版 rst_n 后 D（1 指令 sid1 + 真信用）均恢复。
        //   若 legacy 实测 intr 早于 late1_cyc（竞态再次抢跑），如实记录
        //   为竞态形态，归因问题保持开放（边界 2 诚实条款）。
        $display("[F6B_FACT] case=B5 begin");
        host_cfg;
        wr_iq24(2'd0); wr_iq24(2'd0); wr_iq24(2'd0);      // 批 A：3 指令 sid0
        csr_wr(12'h020, 32'h0000_0001);
        t_idle(10);
        credit24(4'd0, 1'b0); t_idle(4);                  // 1/3 信用，批在飞
        csr_wr(12'h000, 32'h0000_0002); t_idle(8);        // 在飞 cfg_rst
        host_cfg;
        wr_iq24(2'd2); wr_iq24(2'd2);                     // 新批 C sid2（2 指令）
        csr_wr(12'h020, 32'h0000_0201);
        cap_issue = 1;
        iss_m = issue24_cnt; t_idle(20);
        iss_p2 = issue24_cnt - iss_m; cap_issue = 0;
        late1_cyc = cyc;
        credit24(4'd0, 1'b0); t_idle(3);                  // 旧信用 #1（id=0）
        credit24(4'd1, 1'b0); t_idle(15);                 // 旧信用 #2（id=1）
        intr_cyc5 = intr_cyc;
        first_issue_cyc5 = first_issue_cyc;
        csr_rd(12'h024, w); doneC5 = w[0]; eidC5 = w[8:7];
        csr_rd(12'h004, w); intid_p5 = w[15:8];
        interr_p5 = w[1]; failed_p5 = w[2];               // 判据 v2
        hard_reset;
        host_cfg;
        wr_iq24(2'd1);                                    // 批 D sid1（恢复控制）
        csr_wr(12'h020, 32'h0000_0101);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD5 = w[0]; eidD5 = w[8:7];
        $display("[F6B_FACT] case=B5 iss_p2=%0d first_issue_cyc=%0d late1_cyc=%0d intr_cyc=%0d doneC=%0d eidC=%0d intid_p5=%0d interr_p5=%0d failed_p5=%0d doneD=%0d eidD=%0d end",
                 iss_p2, first_issue_cyc5, late1_cyc, intr_cyc5,
                 doneC5, eidC5, intid_p5, interr_p5, failed_p5, doneD5, eidD5);
        hard_reset;

        // ====== B6 T-UNITERR（E2 定向：id 正确 + 单元错误位 bit6=1 的信用） ======
        // 判据 v2（2026-10-10 首跑前冻结，F6 文档 §12.6）：
        //   legacy/fixed_p1：重映射丢 bit6 ⇒ 信用被判为合法 ⇒ A 假完成
        //     （doneA6=1）且无错误通知无锁存 ⇒ VIOLATION（E2 缺口直接复现）；
        //   fixed_p2：crd_err_i 入检出 ⇒ err/通知一次（intid+1、int_err）
        //     /FAILED 锁存/A 拒完成（C-DRAIN err 排除）；锁存期间新 START C
        //     不执行；rst_n 后 D 恢复。
        $display("[F6B_FACT] case=B6 begin");
        host_cfg;
        wr_iq24(2'd0);                                    // 批 A：1 指令 sid0
        csr_wr(12'h020, 32'h0000_0001);
        iss_m = issue24_cnt; t_idle(8);
        iss6 = issue24_cnt - iss_m;                       // 健全性：期望 1
        credit24(4'd0, 1'b1); t_idle(10);                 // id 正确 + bit6=1
        csr_rd(12'h004, w);
        intid6 = w[15:8]; interr6 = w[1]; failed6 = w[2];
        csr_rd(12'h024, w); doneA6 = w[0];
        wr_iq24(2'd2);                                    // 批 C 指令 sid2
        csr_wr(12'h020, 32'h0000_0201);                   // START C（应被锁存拒发）
        iss_m = issue24_cnt; t_idle(30);
        iss_p6 = issue24_cnt - iss_m;
        csr_rd(12'h024, w); doneC6 = w[0];
        csr_rd(12'h004, w); failed6b = w[2];
        hard_reset;                                       // rst_n 唯一解锁
        host_cfg;
        wr_iq24(2'd1);                                    // 批 D sid1（恢复控制）
        csr_wr(12'h020, 32'h0000_0101);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD6 = w[0]; eidD6 = w[8:7];
        $display("[F6B_FACT] case=B6 iss6=%0d intid6=%0d interr6=%0d failed6=%0d doneA6=%0d iss_p6=%0d doneC6=%0d failed6b=%0d doneD6=%0d eidD6=%0d end",
                 iss6, intid6, interr6, failed6, doneA6, iss_p6, doneC6,
                 failed6b, doneD6, eidD6);
        hard_reset;

        // ====== B7 T-ERRPRIO（判据 v3：控制层错误同拍优先——错误与
        // batch_done 同拍 ⇒ 不登记成功）======
        // 时序构造（首跑后修正）：credit24_26_aligned 使两信用**接受拍**
        // 恰差 +1 ⇒ IQ26 粘滞 err 与批 A（N=1）完成在同一 posedge 注册
        // （推导见 task 注释；旧构造 +2 间距实测 err 晚 1 拍，非同拍）。
        // legacy：N=1 竞态完成远早于信用窗（doneA7=1），且 idle 队列首笔
        //   信用被 `>` 旧判据静默吸收（bit6 亦被重映射丢弃）⇒ 无 int_err/
        //   无锁存 ⇒ VIOLATION；fixed_p1：完成等信用但控制层无错误排除
        //   ⇒ doneA7=1 ⇒ VIOLATION；fixed_p2b：0x024 done 保持 0、通知
        //   一次记错误、FAILED 锁存、cfg_rst 不解锁、rst_n 恢复 ⇒ PASS。
        $display("[F6B_FACT] case=B7 begin");
        host_cfg;
        wr_iq24(2'd0);                                    // 批 A：1 指令 sid0
        csr_wr(12'h020, 32'h0000_0001);
        iss_m = issue24_cnt; t_idle(8);
        iss7 = issue24_cnt - iss_m;                       // 健全性：期望 1
        credit24_26_aligned(4'd0);                        // 好信用 + 1拍后错误
        t_idle(10);                                       //   信用（同拍注册）
        csr_rd(12'h004, w);
        intid7 = w[15:8]; interr7 = w[1]; failed7 = w[2];
        csr_rd(12'h024, w); doneA7 = w[0];
        wr_iq24(2'd2);
        csr_wr(12'h020, 32'h0000_0201);                   // START C（锁存拒发）
        iss_m = issue24_cnt; t_idle(30);
        iss_p7 = issue24_cnt - iss_m;
        csr_rd(12'h024, w); doneC7 = w[0];
        csr_wr(12'h000, 32'h0000_0002); t_idle(8);        // cfg_rst 不解锁
        csr_rd(12'h004, w); failed7b = w[2];
        hard_reset;                                       // rst_n 唯一解锁
        host_cfg;
        wr_iq24(2'd1);                                    // 批 D sid1（恢复控制）
        csr_wr(12'h020, 32'h0000_0101);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD7 = w[0]; eidD7 = w[8:7];
        $display("[F6B_FACT] case=B7 iss7=%0d intid7=%0d interr7=%0d failed7=%0d doneA7=%0d iss_p7=%0d doneC7=%0d failed7b=%0d doneD7=%0d eidD7=%0d end",
                 iss7, intid7, interr7, failed7, doneA7, iss_p7, doneC7,
                 failed7b, doneD7, eidD7);
        hard_reset;

        // ====== B8 T-ERRMASK（判据 v3：通知遮蔽——一源持高后另一 LSU 源
        // 新错误仍须通知一次；同源重复不重复计数）======
        // 无批次纯通知路径。legacy/fixed_p1：idle 首笔被 `>` 旧判据静默
        //   吸收 ⇒ intid8a=0；同源第二笔触发 `>` ⇒ intid8b=1（R6：无
        //   int_err/无锁存）；IQ26 首笔再被吸收 ⇒ intid8c=1 ⇒ VIOLATION。
        // fixed_p2b：首笔即检出（off-by-one 修复）⇒ 1；同源第二笔无新
        //   边沿 ⇒ 保持 1（notify-once 牙齿）；IQ26 新源：1-bit OR 边沿
        //   会被 err_stbaxi 持高遮蔽（复核裁定 #3），逐源边沿后 ⇒ 2。
        $display("[F6B_FACT] case=B8 begin");
        host_cfg;                                         // 无 START：纯通知
        credit25(4'd0, 1'b1); t_idle(8);                  // 源1 首笔 → 检出
        csr_rd(12'h004, w);
        intid8a = w[15:8]; interr8a = w[1]; failed8a = w[2];
        credit25(4'd1, 1'b1); t_idle(8);                  // 同源第二笔：无新边沿
        csr_rd(12'h004, w); intid8b = w[15:8];
        credit26(4'd0, 1'b1); t_idle(8);                  // 源2 首笔：遮蔽检验
        csr_rd(12'h004, w);
        intid8c = w[15:8]; interr8c = w[1]; failed8c = w[2];
        hard_reset;
        host_cfg;
        wr_iq24(2'd1);                                    // 恢复控制批 D
        csr_wr(12'h020, 32'h0000_0101);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD8 = w[0];
        $display("[F6B_FACT] case=B8 intid_a=%0d interr_a=%0d failed_a=%0d intid_b=%0d intid_c=%0d interr_c=%0d failed_c=%0d doneD=%0d end",
                 intid8a, interr8a, failed8a, intid8b, intid8c, interr8c,
                 failed8c, doneD8);
        hard_reset;

        // ====== B9 T-PROTOERR（判据 v3：协议异常上报——arm_err →
        // proto_err_o → 系统 FAILED；lsu_err_evt 独立进入通知判定） ======
        // force 目标仅存在于含 F6 模块 proto_err 路径的 DUT（fixed_p2b）；
        // legacy/fixed_p1 由运行器不加 +define 走 skipped 分支，矩阵记 S。
        // 诚实注记：force 属诊断性强制——模拟「协议异常已发生」，不声称
        // 异常可经信用通道产生；异常产生机制本身的定向证据 = TB-A C7
        // proto 字段（重复载荷 ⇒ arm_err ⇒ proto_err_o=1）。
`ifdef F6B_HAS_F6PROTO
        $display("[F6B_FACT] case=B9 begin");
        host_cfg;                                         // 无批次：纯通知路径
        force u_dut.u_lsu_complete_24.arm_err = 1'b1;     // 诊断性强制
        t_idle(8);
        csr_rd(12'h004, w);                               // ir_err 全程未参与
        intid9 = w[15:8]; interr9 = w[1]; failed9 = w[2]; // 期望 1/1/1
        wr_iq24(2'd2);
        csr_wr(12'h020, 32'h0000_0201);                   // START C（锁存拒发）
        iss_m = issue24_cnt; t_idle(30);
        iss_p9 = issue24_cnt - iss_m;
        csr_rd(12'h024, w); doneC9 = w[0];
        release u_dut.u_lsu_complete_24.arm_err;          // 源撤销：锁存须保持
        t_idle(8);
        csr_rd(12'h004, w); failed9b = w[2];              // 期望 1
        hard_reset;
        host_cfg;
        wr_iq24(2'd1);                                    // 批 D sid1（恢复控制）
        csr_wr(12'h020, 32'h0000_0101);
        t_idle(8);
        credit24(4'd0, 1'b0); t_idle(12);
        csr_rd(12'h024, w); doneD9 = w[0]; eidD9 = w[8:7];
        $display("[F6B_FACT] case=B9 intid9=%0d interr9=%0d failed9=%0d iss_p9=%0d doneC9=%0d failed9b=%0d doneD9=%0d eidD9=%0d end",
                 intid9, interr9, failed9, iss_p9, doneC9, failed9b,
                 doneD9, eidD9);
`else
        $display("[F6B_FACT] case=B9 skipped=1 end");
`endif

        $display("[F6B_FACT] suite=done");
        $finish;
    end

    // 全局看门狗
    initial begin
        #(20000 * 10);   // 20000 拍 ≈ 3× 全速余量（全序约 6000 拍）
        $display("[F6B_FACT] suite=timeout");
        $fatal(1, "F6B watchdog");
    end

endmodule
