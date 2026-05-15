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
add_iq_latch_protect_axiom = PhysicalAxioms.add_iq_latch_protect_axiom


class SynthesisAxioms:
    """
    兼容旧版的 SynthesisAxioms 类。ADR-013 后只保留 7 active axiom wrapper。
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

    @staticmethod
    def add_iq_latch_protect_axiom(*args, **kwargs):
        return add_iq_latch_protect_axiom(*args, **kwargs)


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
    "add_iq_latch_protect_axiom",
]
