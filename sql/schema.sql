-- Creare schema dedicata
CREATE SCHEMA IF NOT EXISTS greengrid;

-- Tabela Dimensiune: Timp
CREATE TABLE IF NOT EXISTS greengrid.dim_time (
    timestamp_utc TIMESTAMP PRIMARY KEY,
    year INT NOT NULL,
    month INT NOT NULL,
    day INT NOT NULL,
    hour INT NOT NULL,
    day_of_week VARCHAR(15) NOT NULL,
    season VARCHAR(15) NOT NULL
);

-- Tabela de Fapte: Generare si Consum
CREATE TABLE IF NOT EXISTS greengrid.fact_renewable_generation (
    timestamp_utc TIMESTAMP PRIMARY KEY,
    solar_mw NUMERIC(10,2),
    wind_mw NUMERIC(10,2),
    total_renewable_mw NUMERIC(10,2),
    load_mw NUMERIC(10,2),
    renewable_share_pct NUMERIC(5,2),
    is_anomaly BOOLEAN,
    data_quality VARCHAR(20),
    FOREIGN KEY (timestamp_utc) REFERENCES greengrid.dim_time(timestamp_utc)
);

select * from greengrid.dim_time


SELECT 
    f.timestamp_utc,
    t.year,
    t.season,
    f.solar_mw,
    f.wind_mw,
    f.total_renewable_mw,
    f.load_mw,
    f.renewable_share_pct,
    f.is_anomaly
FROM greengrid.fact_renewable_generation f
JOIN greengrid.dim_time t ON f.timestamp_utc = t.timestamp_utc
ORDER BY f.timestamp_utc DESC
LIMIT 10;
SELECT COUNT(*) FROM greengrid.fact_renewable_generation;

TRUNCATE TABLE greengrid.fact_renewable_generation, greengrid.dim_time CASCADE;

select * from greengrid.fact_renewable_generation