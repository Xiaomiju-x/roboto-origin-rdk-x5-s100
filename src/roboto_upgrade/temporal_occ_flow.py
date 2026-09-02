"""Tiny temporal occupancy/flow model for X5 offline deployment trials."""

from __future__ import annotations

import torch
from torch import nn


class ResidualDilatedBlock(nn.Module):
    def __init__(self, channels: int, dilation: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, 3, padding=dilation, dilation=dilation)
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1)
        self.activation = nn.ReLU(inplace=False)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        residual = value
        value = self.activation(self.conv1(value))
        value = self.conv2(value)
        return self.activation(value + residual)


class TinyTemporalOccFlow(nn.Module):
    """Fixed-shape friendly CNN with three independently auditable heads."""

    def __init__(self, history_frames: int = 4, future_frames: int = 3, channels: int = 24) -> None:
        super().__init__()
        self.history_frames = history_frames
        self.future_frames = future_frames
        self.stem = nn.Sequential(
            nn.Conv2d(history_frames, channels, 3, padding=1),
            nn.ReLU(inplace=False),
        )
        self.backbone = nn.Sequential(
            ResidualDilatedBlock(channels, 1),
            ResidualDilatedBlock(channels, 2),
            ResidualDilatedBlock(channels, 3),
        )
        self.occupancy_head = nn.Conv2d(channels, future_frames, 1)
        self.flow_head = nn.Conv2d(channels, 2, 1)
        self.uncertainty_head = nn.Conv2d(channels, 1, 1)

    def forward(self, history: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = self.backbone(self.stem(history))
        occupancy_logits = self.occupancy_head(features)
        flow = torch.tanh(self.flow_head(features)) * 1.5
        uncertainty_logits = self.uncertainty_head(features)
        return occupancy_logits, flow, uncertainty_logits
