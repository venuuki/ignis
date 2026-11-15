"""Keep native S-NPP diagnostics independent of the sparsified combined record."""
from pipeline.common import PROCESSED
import duckdb
import polars as pl

def build_native_snpp():
    pattern=str(PROCESSED/'hotspots'/'*.parquet').replace('\\','/')
    with duckdb.connect() as con:
        native=con.sql(f"""select region as country,cell,year::int as year,month::int as month,
            count(*)::float as snpp from '{pattern}' where qc_pass and satellite='S-NPP' group by all""").pl()
    path=PROCESSED/'native_snpp_monthly.parquet';temp=path.with_name(path.name+'.part')
    native.write_parquet(temp,compression='zstd');temp.replace(path)
    print(f'Native S-NPP support: {native.height:,} cell-months; {native["snpp"].sum():,.0f} quality-filtered records',flush=True)

if __name__=='__main__':build_native_snpp()
