import os
import requests
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# 1. Încarcă variabilele de mediu din fișierul .env
load_dotenv()

DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASS')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')

ENGINE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
FILE_PATH = 'time_series.xlsx'
DOWNLOAD_URL = "https://data.open-power-system-data.org/time_series/latest/time_series.xlsx"

def download_excel_if_not_exists(file_path, url):
    """Descarcă automat fișierul Excel de pe OPSD dacă nu există local."""
    if not os.path.exists(file_path):
        print(f"📥 [AUTO-DOWNLOAD] Fișierul '{file_path}' nu a fost găsit. Se descarcă automat...")
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
            print("✅ Descărcare finalizată cu succes!")
        else:
            raise Exception(f"Eroare la descărcare: Status Code {response.status_code}")
    else:
        print(f"ℹ️ Fișierul '{file_path}' există deja local. Se trece la procesare.")

def run_etl_from_excel(file_path=FILE_PATH):
    # Asigurăm prezența fișierului
    download_excel_if_not_exists(file_path, DOWNLOAD_URL)

    print("🚀 [1/3] EXTRACT: Citire sheet '60min' începând cu rândul A7...")
    
    # Read metadata for headers
    df_meta = pd.read_excel(file_path, sheet_name='60min', nrows=7, header=None)
    
    new_columns = []
    for col_idx in range(df_meta.shape[1]):
        col_values = df_meta.iloc[:, col_idx].dropna().astype(str).str.strip().tolist()
        
        if any('utc_timestamp' in val.lower() for val in col_values):
            new_columns.append('utc_timestamp')
        else:
            cleaned_parts = []
            for val in col_values:
                if val and not val.startswith('http') and val != 'MW' and val not in cleaned_parts:
                    cleaned_parts.append(val)
            combined = '_'.join(cleaned_parts)
            new_columns.append(combined)

    # Read actual data
    df_data = pd.read_excel(file_path, sheet_name='60min', skiprows=7, header=None)
    df_data.columns = new_columns

    # Identification of Romanian columns
    ro_solar = [c for c in df_data.columns if 'RO' in c and 'solar' in c and 'IT_ROSN' not in c]
    ro_wind = [c for c in df_data.columns if 'RO' in c and 'wind' in c and 'IT_ROSN' not in c]
    ro_load = [c for c in df_data.columns if 'RO' in c and 'load' in c and 'actual' in c]

    df = pd.DataFrame()
    df['timestamp_utc'] = pd.to_datetime(df_data['utc_timestamp'])
    
    df['solar_mw'] = pd.to_numeric(df_data[ro_solar[0]], errors='coerce') if ro_solar else np.nan
    df['wind_mw'] = pd.to_numeric(df_data[ro_wind[0]], errors='coerce') if ro_wind else np.nan
    df['load_mw'] = pd.to_numeric(df_data[ro_load[0]], errors='coerce') if ro_load else np.nan

    print("🧹 [2/3] TRANSFORM: Curățare date, tratare valori 0/null și calcul KPIs...")
    df = df.sort_values('timestamp_utc').set_index('timestamp_utc')

    # Flag Calitate Date
    df['data_quality'] = np.where(df[['solar_mw', 'wind_mw', 'load_mw']].isna().any(axis=1), 'INTERPOLATED', 'OK')

    # TRATARE VALORI ZERO SAU LIPSA:
    # Consumul (load_mw) <= 0 este o eroare de colectare
    df['load_mw'] = df['load_mw'].replace(0, np.nan)
    df['load_mw'] = df['load_mw'].interpolate(method='time').bfill().ffill()

    # Interpolare pentru regenerabile
    df['solar_mw'] = df['solar_mw'].interpolate(method='time').fillna(0).clip(lower=0)
    df['wind_mw'] = df['wind_mw'].interpolate(method='time').fillna(0).clip(lower=0)

    # Calculare KPIs
    df['total_renewable_mw'] = df['solar_mw'] + df['wind_mw']
    df['renewable_share_pct'] = np.where(
        df['load_mw'] > 0, 
        ((df['total_renewable_mw'] / df['load_mw']) * 100).clip(0, 100), 
        0
    )

    # Detectare Anomalii (scădere orară > 35%)
    df['pct_change'] = df['total_renewable_mw'].pct_change()
    df['is_anomaly'] = df['pct_change'] < -0.35

    df = df.reset_index()

    # Tabela Dimensiune: Timp
    dim_time = pd.DataFrame({
        'timestamp_utc': df['timestamp_utc'],
        'year': df['timestamp_utc'].dt.year,
        'month': df['timestamp_utc'].dt.month,
        'day': df['timestamp_utc'].dt.day,
        'hour': df['timestamp_utc'].dt.hour,
        'day_of_week': df['timestamp_utc'].dt.day_name(),
        'season': df['timestamp_utc'].dt.month.map({
            12:'Iarnă', 1:'Iarnă', 2:'Iarnă',
            3:'Primăvară', 4:'Primăvară', 5:'Primăvară',
            6:'Vară', 7:'Vară', 8:'Vară',
            9:'Toamnă', 10:'Toamnă', 11:'Toamnă'
        })
    })

    # Tabela de Fapte
    fact_table = df[[
        'timestamp_utc', 'solar_mw', 'wind_mw', 'total_renewable_mw',
        'load_mw', 'renewable_share_pct', 'is_anomaly', 'data_quality'
    ]]

    print("💾 [3/3] LOAD: Salvare în baza de date PostgreSQL...")
    engine = create_engine(ENGINE_URL)

    # Curățăm datele vechi folosind text() pentru a preveni erorile de duplicate (Primary Key)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE greengrid.fact_renewable_generation CASCADE;"))
        conn.execute(text("TRUNCATE TABLE greengrid.dim_time CASCADE;"))

    # Inserare date în baza de date PostgreSQL
    dim_time.to_sql(
        'dim_time', 
        engine, 
        schema='greengrid', 
        if_exists='append', 
        index=False, 
        chunksize=1000
    )
    
    fact_table.to_sql(
        'fact_renewable_generation', 
        engine, 
        schema='greengrid', 
        if_exists='append', 
        index=False, 
        chunksize=1000
    )

    print("✨ PROCESUL ETL A REUȘIT CU SUCCES!")

if __name__ == "__main__":
    run_etl_from_excel()