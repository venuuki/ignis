from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AOI(BaseModel):
    """Area of interest. Provide the fields that match `type`."""

    type: Literal["bbox", "polygon", "point_radius", "region"]
    bbox: list[float] | None = Field(None, description="[west, south, east, north] in degrees")
    geometry: dict[str, Any] | None = Field(None, description="GeoJSON Polygon or MultiPolygon")
    point: list[float] | None = Field(None, description="[lon, lat]")
    radius_km: float | None = Field(None, gt=0, le=1000)
    region: str | None = Field(None, description="Country name as used by FIRMS, e.g. 'Australia'")

    @model_validator(mode="after")
    def _check(self) -> "AOI":
        if self.type == "bbox":
            if not self.bbox or len(self.bbox) != 4:
                raise ValueError("bbox needs [west, south, east, north]")
            w, s, e, n = self.bbox
            if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
                raise ValueError("bbox must satisfy west<east, south<north within valid ranges")
        elif self.type == "polygon" and (not self.geometry or self.geometry.get("type") not in ("Polygon", "MultiPolygon")):
            raise ValueError("polygon AOI needs a GeoJSON Polygon/MultiPolygon geometry")
        elif self.type == "point_radius":
            if not self.point or len(self.point) != 2 or not self.radius_km:
                raise ValueError("point_radius AOI needs point [lon, lat] and radius_km")
            if not (-180 <= self.point[0] <= 180 and -90 <= self.point[1] <= 90):
                raise ValueError("point must be [lon, lat] within valid ranges")
        elif self.type == "region" and not self.region:
            raise ValueError("region AOI needs a region name")
        return self


Series = Literal["modis_harmonized", "modis_raw", "viirs_raw"]


class Query(BaseModel):
    aoi: AOI
    start: str = Field("2000-11", pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (default: latest)")
    baseline_start: int | None = None
    baseline_end: int | None = None
    series: Series = "modis_harmonized"

    @model_validator(mode="after")
    def valid_dates(self):
        from datetime import date
        for value in (self.start, self.end):
            if value is not None:
                date.fromisoformat(value + "-01")
        if self.end and self.start > self.end:
            raise ValueError("Start month must precede end month")
        current_year = date.today().year
        for year in (self.baseline_start, self.baseline_end):
            if year is not None and not 2000 <= year <= current_year:
                raise ValueError(f"Baseline year must be between 2000 and {current_year}")
        if self.baseline_start is not None and self.baseline_end is not None and self.baseline_start > self.baseline_end:
            raise ValueError("Baseline from must not exceed baseline to")
        return self


class HotspotQuery(BaseModel):
    aoi: AOI
    start_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    qc_only: bool = True
    sensors: list[Literal["MODIS", "VIIRS"]] = ["MODIS", "VIIRS"]
    limit: int | None = Field(None, ge=1, le=50000)

    @model_validator(mode="after")
    def valid_dates(self):
        from datetime import date
        for value in (self.start_date, self.end_date):
            if value is not None:
                date.fromisoformat(value)
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Start date must precede end date")
        return self

