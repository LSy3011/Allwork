from .dependency_axioms import DependencyAxioms
from .resource_axioms import ResourceAxioms
from .physical_axioms import PhysicalAxioms

# ADR-013 axiom 墓地清理后只保留 7 active axiom 的模块级别名
add_latency_axiom = DependencyAxioms.add_latency_axiom

add_resource_constraint = ResourceAxioms.add_resource_constraint
add_modulo_constraint = ResourceAxioms.add_modulo_constraint
add_bank_conflict_axiom = ResourceAxioms.add_bank_conflict_axiom
add_spill_reload_mutex_axiom = ResourceAxioms.add_spill_reload_mutex_axiom

add_side_effect_phase_axiom = PhysicalAxioms.add_side_effect_phase_axiom
# ADR-021: add_iq_latch_protect_axiom 已删除（dead axiom，零调用）


class SynthesisAxioms:
    """
    兼容旧版的 SynthesisAxioms 类。保留 active axiom wrapper（ADR-021 删 dead 的
    add_iq_latch_protect_axiom，它原被误计入 ADR-013 "7 active"，实为零调用 dead export）。
    若历史代码引用已删的 axiom（如 add_anti_overwrite_axiom），会触发 AttributeError ——
    这是预期行为（参见 ADR-010 axiom 考古表 + ADR-013 §Consequences）。
    """
    @staticmethod
    def _add_constraint(solver, cond, label):
        from .base import AxiomBase
        AxiomBase._add_constraint(solver, cond, label)

    @staticmethod
    def add_latency_axiom(*args, **kwargs):
        return add_latency_axiom(*args, **kwargs)

    @staticmethod
    def add_resource_constraint(*args, **kwargs):
        return add_resource_constraint(*args, **kwargs)

    @staticmethod
    def add_modulo_constraint(*args, **kwargs):
        return add_modulo_constraint(*args, **kwargs)

    @staticmethod
    def add_bank_conflict_axiom(*args, **kwargs):
        return add_bank_conflict_axiom(*args, **kwargs)

    @staticmethod
    def add_spill_reload_mutex_axiom(*args, **kwargs):
        return add_spill_reload_mutex_axiom(*args, **kwargs)

    @staticmethod
    def add_side_effect_phase_axiom(*args, **kwargs):
        return add_side_effect_phase_axiom(*args, **kwargs)


__all__ = [
    "DependencyAxioms",
    "ResourceAxioms",
    "PhysicalAxioms",
    "SynthesisAxioms",
    "add_latency_axiom",
    "add_resource_constraint",
    "add_modulo_constraint",
    "add_bank_conflict_axiom",
    "add_spill_reload_mutex_axiom",
    "add_side_effect_phase_axiom",
]
