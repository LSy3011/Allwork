import unittest

from api.hw_spec import HardwareSpec
from synthesis.ucode_solver import UCodeSynthesizer
from sim.tests.helpers import make_op

class TestRTOVRReject(unittest.TestCase):
    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synth = UCodeSynthesizer(self.spec)

    def test_rtovr_undersampling_reject(self):
        """测试 Verifier 是否能正确拒绝采样过早的调度"""
        prod = make_op("FPMUL", "prod")
        cons = make_op("FPADD", "cons", deps=["prod"])
        cons.metadata["rtovr_deps"] = {"prod": {"dvr": 1, "sel": 2}}
        
        ops = [prod, cons]
        unit_map = {"prod": "fpmul", "cons": "fpadd"}
        
        # 正常 FPMUL 延迟是 4
        # prod 发射在 0，准备好在 0 + 4 = 4
        # RTOVR 应该在 4 之后采样。
        # 如果 cons 发射在 4，RTOVR 采样推断为 3 ( < 4)，应该被拒绝。
        schedule = {"prod": 0, "cons": 4}
        
        result = self.synth.validate_physical_schedule(ops, schedule, unit_map, II=10)
        self.assertFalse(result, "Verifier 应当拒绝采样过早的非法调度")

    def test_rtovr_overwrite_reject(self):
        """测试 Verifier 是否能正确拒绝采样过晚的调度"""
        prod = make_op("FPMUL", "prod")
        cons = make_op("FPADD", "cons", deps=["prod"])
        cons.metadata["rtovr_deps"] = {"prod": {"dvr": 1, "sel": 2}}
        
        ops = [prod, cons]
        unit_map = {"prod": "fpmul", "cons": "fpadd"}
        
        II = 10
        # ADR-006 精确公式：数据存活窗口上界 = t_prod + lat_p + II = 0 + 4 + 10 = 14。
        # RTOVR 采样推断 = t_cons - 1（无显式 RTOVR 节点时降级）。
        # cons 发射在 16 → t_rtovr 推断 15 >= 14，数据已被下一迭代覆盖 → 必须拒绝。
        # （原测试用旧公式 t_prod+II=10 心算，schedule cons=11 永远触发不了，陈旧 bug）
        schedule = {"prod": 0, "cons": 16}

        result = self.synth.validate_physical_schedule(ops, schedule, unit_map, II=II)
        self.assertFalse(result, "Verifier 应当拒绝采样过晚（已被覆盖）的非法调度")

if __name__ == '__main__':
    unittest.main()
