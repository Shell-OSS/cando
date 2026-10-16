# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Strategy configurations for the Generator agent.

Predefined strategy configs cycling across parallel generations.
"""

from .models import StrategyConfig

STRATEGY_CONFIGS: list[StrategyConfig] = [
    StrategyConfig(key="conservative", guidance_mode="all"),
    StrategyConfig(key="surgical", guidance_mode="all"),
    StrategyConfig(
        key="hard_constraints_only", guidance_mode="hard_constraints"
    ),
    StrategyConfig(key="assembly_only", guidance_mode="assembly"),
    StrategyConfig(key="proximity_only", guidance_mode="proximity"),
    StrategyConfig(key="aggressive", guidance_mode="none"),
    StrategyConfig(key="macro_spatial", guidance_mode="none"),
    StrategyConfig(key="space_utilization", guidance_mode="none"),
]

# Neutral strategy used by the no_strategies ablation mode. Not part of
# STRATEGY_CONFIGS so it is never sampled by the "library" strategy mode.
NEUTRAL_STRATEGY = StrategyConfig(key="neutral", guidance_mode="all")
