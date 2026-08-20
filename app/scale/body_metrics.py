"""Kompozycja ciala z wagi i impedancji.

Odwzorowanie algorytmu z aplikacji Mi Fit (te same wzory, ktorych uzywaja
openScale i inne otwarte implementacje). Wyniki sa szacunkami - maja byc
zgodne z tym, co pokazuje aplikacja producenta, a nie z DEXA.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class BodyComposition:
    lbm: float
    bmi: float
    fat_percentage: float
    water_percentage: float
    muscle_mass: float
    bone_mass: float
    visceral_fat: float
    protein_percentage: float
    bmr: float
    metabolic_age: float
    ideal_weight: float

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 2) for k, v in asdict(self).items()}


class BodyMetrics:
    def __init__(self, weight: float, height: float, age: int, sex: str, impedance: int):
        self.weight = float(weight)
        self.height = float(height)
        self.age = int(age)
        self.sex = "female" if str(sex).lower().startswith("f") else "male"
        self.impedance = int(impedance)

        if not 10 <= self.weight <= 200:
            raise ValueError(f"Waga poza zakresem: {self.weight}")
        if not 90 <= self.height <= 220:
            raise ValueError(f"Wzrost poza zakresem: {self.height}")

    # --- czesc chuda ciala (baza dla reszty wzorow) ---
    def lbm_coefficient(self) -> float:
        lbm = (self.height * 9.058 / 100) * (self.height / 100)
        lbm += self.weight * 0.32 + 12.226
        lbm -= self.impedance * 0.0068
        lbm -= self.age * 0.0542
        return lbm

    def bmi(self) -> float:
        return _clamp(self.weight / ((self.height / 100) ** 2), 10, 90)

    def fat_percentage(self) -> float:
        if self.sex == "female" and self.age <= 49:
            const = 9.25
        elif self.sex == "female":
            const = 7.25
        else:
            const = 0.8

        coefficient = 1.0
        if self.sex == "male" and self.weight < 61:
            coefficient = 0.98
        elif self.sex == "female" and self.weight > 60:
            coefficient = 0.96
            if self.height > 160:
                coefficient *= 1.03
        elif self.sex == "female" and self.weight < 50:
            coefficient = 1.02
            if self.height > 160:
                coefficient *= 1.03

        fat = (1.0 - (((self.lbm_coefficient() - const) * coefficient) / self.weight)) * 100
        if fat > 63:
            fat = 75
        return _clamp(fat, 5, 75)

    def water_percentage(self) -> float:
        water = (100 - self.fat_percentage()) * 0.7
        coefficient = 0.98 if water <= 50 else 1.02
        water *= coefficient
        if water >= 65:
            water = 75
        return _clamp(water, 35, 75)

    def bone_mass(self) -> float:
        base = 0.245691014 if self.sex == "female" else 0.18016894
        bone = (base - (self.lbm_coefficient() * 0.05158)) * -1
        bone = bone + 0.1 if bone > 2.2 else bone - 0.1
        limit = 5.1 if self.sex == "female" else 5.2
        if bone > limit:
            bone = 8
        return _clamp(bone, 0.5, 8)

    def muscle_mass(self) -> float:
        muscle = self.weight - (self.fat_percentage() * 0.01 * self.weight) - self.bone_mass()
        limit = 84 if self.sex == "female" else 93.5
        if muscle >= limit:
            muscle = 120
        return _clamp(muscle, 10, 120)

    def visceral_fat(self) -> float:
        if self.sex == "female":
            if self.weight > (13 - (self.height * 0.5)) * -1:
                sub = ((self.height * 1.45) + (self.height * 0.1158) * self.height) - 120
                vfat = (self.weight * 500 / sub - 6) + (self.age * 0.07)
            else:
                sub = 0.691 + (self.height * -0.0024) + (self.height * -0.0024)
                vfat = (((self.height * 0.027) - (sub * self.weight)) * -1) + (self.age * 0.07) - self.age
        else:
            if self.height < self.weight * 1.6:
                sub = ((self.height * 0.4) - (self.height * (self.height * 0.0826))) * -1
                vfat = ((self.weight * 305) / (sub + 48)) - 2.9 + (self.age * 0.15)
            else:
                sub = 0.765 + self.height * -0.0015
                vfat = (((self.height * 0.143) - (self.weight * sub)) * -1) + (self.age * 0.15) - 5.0
        return _clamp(vfat, 1, 50)

    def protein_percentage(self) -> float:
        protein = (self.muscle_mass() / self.weight) * 100 - self.water_percentage()
        return _clamp(protein, 5, 32)

    def bmr(self) -> float:
        if self.sex == "female":
            bmr = 864.6 + self.weight * 10.2036 - self.height * 0.39336 - self.age * 6.204
            if bmr > 2996:
                bmr = 5000
        else:
            bmr = 877.8 + self.weight * 14.916 - self.height * 0.726 - self.age * 8.976
            if bmr > 2322:
                bmr = 5000
        return _clamp(bmr, 500, 10000)

    def metabolic_age(self) -> float:
        if self.sex == "female":
            age = (self.height * -1.1165 + self.weight * 1.5784
                   + self.age * 0.4615 + self.impedance * 0.0415 + 83.2548)
        else:
            age = (self.height * -0.7471 + self.weight * 0.9161
                   + self.age * 0.4184 + self.impedance * 0.0517 + 54.2267)
        return _clamp(age, 15, 80)

    def ideal_weight(self) -> float:
        if self.sex == "female":
            return _clamp((self.height - 70) * 0.6, 30, 198)
        return _clamp((self.height - 80) * 0.7, 30, 198)

    def compute(self) -> BodyComposition:
        return BodyComposition(
            lbm=self.lbm_coefficient(),
            bmi=self.bmi(),
            fat_percentage=self.fat_percentage(),
            water_percentage=self.water_percentage(),
            muscle_mass=self.muscle_mass(),
            bone_mass=self.bone_mass(),
            visceral_fat=self.visceral_fat(),
            protein_percentage=self.protein_percentage(),
            bmr=self.bmr(),
            metabolic_age=self.metabolic_age(),
            ideal_weight=self.ideal_weight(),
        )
