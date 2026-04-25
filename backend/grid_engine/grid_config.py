from dataclasses import dataclass


@dataclass
class GridConfig:
    symbol: str
    p_min: float
    p_max: float
    n_levels: int
    capital_usdt: float
    maker_fee_pct: float = 0.001
    min_lot_size: float = 0.0

    @property
    def step(self) -> float:
        return (self.p_max - self.p_min) / self.n_levels

    @property
    def step_pct(self) -> float:
        return self.step / self.p_min

    @property
    def capital_per_level(self) -> float:
        return self.capital_usdt / self.n_levels

    def buy_prices(self) -> list[float]:
        return [self.p_min + i * self.step for i in range(self.n_levels)]

    def sell_price(self, buy_price: float) -> float:
        return buy_price + self.step
