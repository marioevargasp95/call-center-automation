"""
pipeline_call_center.py
=======================
Pipeline de validación y preparación de base para Contact Center(Wolkvox).

Flujo:
  1. Carga de retiros (CSV más reciente en Y:/Retiros/)
  2. Contratos desde API ContratosAnalitica
  3. P&G desde CSV Consolidado
  4. Gerencia desde API Gerencia
  5. Enriquecimiento: antigüedad, reglas de negocio, normalización de contactos
  6. Filtro de gestionables
  7. Construcción y exportación de base_final
  8. Envío de resumen por correo
  9. Carga a Wolkvox API

Ejecutar desde Task Scheduler mediante ejecutar_pipeline.bat
"""

import base64
import os
import re
import json
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import requests
import urllib3

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN  (ajustar según entorno)
# ─────────────────────────────────────────────────────────────────────────────

# ── API interna ───────────────────────────────────────────────────────────────
API_BASE_URL  = os.getenv("API_BASE_URL", "http://YOUR_API_HOST:PORT")
API_USER      = "xxx"
API_PASSWORD  = "xxxxxxxxxxxx"

# ── Wolkvox API ───────────────────────────────────────────────────────────────
WV_SERVER        = "xxxxxxxxxxxxxxxx"
WV_TOKEN         = "xxxxxxxxxxxxxxxxxxxx"
WV_SKILL_ID      = "4111"
WV_CAMPAIGN_TYPE = "preview"
WV_HORA_INICIO   = "080000"
WV_HORA_FIN      = "180000"
WV_BATCH_SIZE    = 100                    # registros por lote (máx recomendado: 100)

# ── Correo ────────────────────────────────────────────────────────────────────
SMTP_SERVER   = "smtp.gmail.com"       # ← ajustar según servidor de correo
SMTP_PORT     = 587
SMTP_USER     = os.getenv("SMTP_USER", "tu_correo@empresa.co")  # ← cuenta que envía el resumen
SMTP_PASSWORD = "xxxxxxxxxxxxxxxxxxxxx"
EMAIL_DESTINO = [os.getenv("EMAIL_DESTINO", "destino@empresa.co")]  # destinatario resumen HTML 
EMAIL_COPIA   = [os.getenv("EMAIL_COPIA", "copia@empresa.co")] # copia resumen HTM
EMAIL_LOG     = [os.getenv("EMAIL_LOG_1", "log@empresa.co"), os.getenv("EMAIL_LOG_2", "dev@empresa.co")] # destinatario log de ejecución y alertas de error

# ── Rutas de archivos ─────────────────────────────────────────────────────────
RETIROS_DIR      = r"Y:\Retiros"
CONSOLIDADO_PATH = r"Y:\Retiros\Consolidado.csv"
DIR_BASE         = Path(__file__).parent if "__file__" in globals() else Path.cwd()
LOG_DIR          = DIR_BASE / "logs"

# ── Campaña OPT5 (mes anterior al de ejecución) ──────────────────────────────
_hoy          = datetime.now()
_mes_ant_num  = _hoy.month - 1 if _hoy.month > 1 else 12
_anio_ant     = _hoy.year      if _hoy.month > 1 else _hoy.year - 1
_MESES_ES     = {
    1:"ENERO",2:"FEBRERO",3:"MARZO",4:"ABRIL",5:"MAYO",6:"JUNIO",
    7:"JULIO",8:"AGOSTO",9:"SEPTIEMBRE",10:"OCTUBRE",11:"NOVIEMBRE",12:"DICIEMBRE",
}
OPT5_CAMPANA  = f"UXS {_MESES_ES[_mes_ant_num]} {_anio_ant}"

# ── Entidades excluidas ───────────────────────────────────────────────────────
ENTIDADES_EXCLUIR = [
    "COMERCIALIZADORA DE SERVICIOS BASICOS SAS",
    "COOPERATIVA DE EMPLEADOS DE CAFAM",
    "FONDO DE EMPLEADOS DE DAVIVIENDA - FONDAVIVIENDA",
    "COOPERATIVA DE LOS PROFESIONALES COASMEDAS - COASMEDAS",
]

# ── Normalización de contactos ────────────────────────────────────────────────
GENERIC_THRESHOLD = 15
GENERIC_DOMAINS   = {"losolivos.co", "losolivosbogota.co"}
GENERIC_EXPLICIT  = {"olivosbogota@losolivos.co", "contacto@losolivosbogota.co"}
ROLE_PREFIXES     = {
    "info", "contacto", "ventas", "administracion", "admin", "gerencia",
    "soporte", "noreply", "no-reply", "atencion", "servicio", "confirmacion",
    "facturacion", "contabilidad", "cobranza", "pagos",
}
ALLOWED_CEL_PREFIXES = {
    "300","301","302","303","304","305","310","311","312","313","314",
    "315","316","317","318","319","320","321","322","323","324","333","350","351",
}
AREA_CODES_FIJO = {"601","602","604","605","606","607","608"}


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUE 1 — AUTENTICACIÓN API
# ─────────────────────────────────────────────────────────────────────────────

def obtener_token() -> str | None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/Login",
            json={"username": API_USER, "password": API_PASSWORD},
            verify=False, timeout=30,
        )
        if resp.status_code == 200:
            print("✅ Token obtenido.")
            return resp.json().get("accessToken")
        print(f"❌ Login fallido [{resp.status_code}]: {resp.text[:200]}")
    except requests.RequestException as e:
        print(f"❌ Error de conexión: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUE 2 — CARGA DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

def cargar_retiros() -> pd.DataFrame:
    archivos = list(Path(RETIROS_DIR).glob("Retiros_*.csv"))
    if not archivos:
        raise FileNotFoundError(f"No se encontraron archivos Retiros_*.csv en {RETIROS_DIR}")
    archivo = max(archivos, key=os.path.getmtime)
    print(f"📂 Retiros: {archivo}")
    df = pd.read_csv(archivo, encoding="utf-8-sig")
    df = df[~df["Entidad"].isin(ENTIDADES_EXCLUIR)].copy()
    df["NIT_Entidad"] = (
        df["NIT_Entidad"].astype(str)
        .str.replace(r"-\d+", "", regex=True)
        .str.replace(" ", "", regex=False)
    )
    return df


def cargar_contratos_api(token: str) -> pd.DataFrame:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(
        f"{API_BASE_URL}/api/ContratosAnalitica/buscar",
        headers=headers, json={"convenio": "", "anio": "2025"},
        verify=False, timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    print(f"📦 Contratos: {len(data)} registros")
    df = pd.DataFrame(data)[["Convenio","NroContrato","Estado","Entidad","CuentaContrato","RefContrato"]]
    df["Entidad"]    = df["Entidad"].str.replace("C", "", regex=False)
    df = df.drop_duplicates(subset="NroContrato", keep="first")
    df["NroContrato"] = df["NroContrato"].astype(str)
    return df


def cargar_pyg() -> pd.DataFrame:
    df = pd.read_csv(CONSOLIDADO_PATH, encoding="utf-8-sig")
    ultimo_anio = df["AÑOC"].max()
    print(f"📊 P&G año: {ultimo_anio}")
    df = df[df["AÑOC"] == ultimo_anio].copy()
    df["NIT"] = (
        df["NIT"].astype(str)
        .str.replace(r"-\d+", "", regex=True)
        .str.replace(" ", "", regex=False)
    )
    return df[["NIT", "EXEDENTE_NETOC"]]


def cargar_gerencia_api(token: str) -> pd.DataFrame:
    r = requests.get(
        f"{API_BASE_URL}/api/Gerencia",
        headers={"Authorization": f"Bearer {token}"},
        verify=False, timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    print(f"📦 Gerencia: {len(data)} registros")
    cols = ["Contrato","Convenio","Documento_Contratante","Documento_Titular",
            "EDAD","GENERO","Nit_Entidad","Entidad","Codigo_Plan","Plan",
            "Valor_Mensual","Valor_Anual"]
    df = pd.DataFrame(data)[cols]
    df["Nit_Entidad"] = df["Nit_Entidad"].str.replace("C", "", regex=False)
    df["Contrato"]    = df["Contrato"].astype(str)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUE 3 — ENRIQUECIMIENTO Y REGLAS DE NEGOCIO
# ─────────────────────────────────────────────────────────────────────────────

def enriquecer_con_contratos(retiros, contratos):
    retiros["Contrato"] = retiros["Contrato"].astype(str)
    df = retiros.merge(contratos[["NroContrato","Entidad"]], left_on="Contrato", right_on="NroContrato", how="left")
    df = df.rename(columns={"Entidad_x": "Entidad", "Entidad_y": "Nit_Entidad"})
    return df.drop_duplicates(subset="Contrato", keep="first")


def enriquecer_con_pyg(retiros, pyg):
    df = retiros.merge(pyg, left_on="NIT_Entidad", right_on="NIT", how="left")
    df.columns = df.columns.str.strip()
    df = df.rename(columns={"EXEDENTE_NETOC": "EXCEDENTE_NETOC"})
    df["EXCEDENTE_NETOC"] = (
        df["EXCEDENTE_NETOC"].astype(str)
        .str.replace(",", ".", regex=False).str.replace(" ", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )
    return df


def calcular_antiguedad_y_reglas(df):
    df["Fecha_Extraccion"] = pd.to_datetime(df["Fecha_Extraccion"], dayfirst=True, errors="coerce")
    df["Fecha Afiliación"] = pd.to_datetime(df["Fecha Afiliación"], dayfirst=True, errors="coerce")
    df["Antigüedad Meses"] = (df["Fecha_Extraccion"] - df["Fecha Afiliación"]).dt.days.div(30).round().fillna(0)
    df["Antigüedad Años"]  = (df["Antigüedad Meses"] / 12).round()
    df["Por tiempo de antigüedad"]      = np.where(df["Antigüedad Meses"] > 6, "Aplica", "No Aplica")
    df["Gestionables por tiempo retiro"] = np.where(df["Antigüedad Meses"] < 6, "No gestion", "Gestionar")
    df["Edad < 20 años"]                = np.where(df["Edad"] > 20, "Si Gestiona", "No Gestiona")
    df["Plazo"] = np.where(df["EXCEDENTE_NETOC"].isna(), "",
                           np.where(df["EXCEDENTE_NETOC"] > 0, "Financiado", "Hasta 6 Meses"))
    df["Anual"] = np.where(df["Edad"] > 65, "Anual", df["Plazo"])
    df.loc[df["Plazo"] == "", "Anual"] = "No Gestionar"
    return df


def marcar_gestion_convenio(retiros, gerencia):
    retiros["Gestion_convenio"] = np.where(
        retiros["Contrato"].isin(gerencia["Contrato"]), "No gestionar", "Si gestionar"
    )
    return retiros


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUE 4 — NORMALIZACIÓN DE CONTACTOS
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL_RE  = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DIGITS_RE = re.compile(r"\d+")


def _extract_emails(raw):
    if pd.isna(raw): return []
    txt = (str(raw).replace("mailto:"," ").replace("MAILTO:"," ")
           .replace(";"," ").replace("|"," ").replace("/"," ")
           .replace("\\"," ").replace(","," ").replace("\n"," ").lower())
    seen, out = set(), []
    for e in _EMAIL_RE.findall(txt):
        e = e.strip(" .,:;|/\\()[]{}<>'\"")
        if _EMAIL_RE.fullmatch(e) and e not in seen:
            seen.add(e); out.append(e)
    return out


def _is_generic_email(email, freq_set):
    dom = email.split("@",1)[-1] if "@" in email else ""
    pre = email.split("@",1)[0]  if "@" in email else ""
    return (email in GENERIC_EXPLICIT or dom in GENERIC_DOMAINS
            or bool(re.search(r"@(gmail|hotmail|yahoo|outlook|live|icloud)\.", dom))
            or pre in ROLE_PREFIXES or email in freq_set)


def normalizar_correos(df):
    s = df["Correo Electrónico"].apply(_extract_emails)
    freq = s.explode().value_counts(dropna=True)
    gen  = set(freq[freq >= GENERIC_THRESHOLD].index)
    def pick(lst):
        v = [e for e in lst if not _is_generic_email(e, gen)]
        return v[0] if v else ("correo_generico" if lst else pd.NA)
    df["Correo_Actualizado"] = s.apply(pick).fillna("sin_correo")
    df["Categoria_correo"]   = np.where(df["Correo_Actualizado"].isin(["correo_generico","sin_correo"]),
                                        df["Correo_Actualizado"], "correo_valido")
    return df


def _clean_cel(d):
    s = str(d)
    if s.startswith("0057") and len(s) >= 14: s = s[4:]
    if s.startswith("57")   and len(s) >= 12: s = s[2:]
    if s.startswith("03")   and len(s) == 11: s = s[1:]
    return s if len(s) == 10 and s[0] == "3" and s[:3] in ALLOWED_CEL_PREFIXES else None


def _extract_cel_candidates(raw):
    if pd.isna(raw): return []
    seen, out = set(), []
    for g in _DIGITS_RE.findall(re.sub(r"[;|,/]", " ", str(raw))):
        c = _clean_cel(g)
        if c and c not in seen: seen.add(c); out.append(c)
    return out


def normalizar_celulares(df):
    cands = df["Teléfono Celular"].apply(_extract_cel_candidates)
    df["Celular_actualizado"]  = cands.apply(lambda x: x[0] if x else pd.NA)
    df["Celular2_actualizado"] = cands.apply(lambda x: x[1] if len(x) > 1 else pd.NA)
    for col in ["Celular_actualizado","Celular2_actualizado"]:
        vc  = df[col].dropna().value_counts()
        gen = set(vc[vc > GENERIC_THRESHOLD].index)
        df[col] = df[col].where(~df[col].isin(gen), "celular_generico").fillna("sin_celular")
    df["Categoria_celular"]  = np.where(df["Celular_actualizado"].isin(["celular_generico","sin_celular"]),
                                        df["Celular_actualizado"], "celular_valido")
    df["Categoria2_celular"] = np.where(df["Celular2_actualizado"].isin(["celular_generico","sin_celular"]),
                                        df["Celular2_actualizado"], "celular_valido")
    return df


def _clean_fijo(d):
    s = str(d)
    if s.startswith("0057") and len(s) >= 14: s = s[4:]
    if s.startswith("57")   and len(s) >= 12: s = s[2:]
    if s.startswith("060")  and len(s) == 11: s = s[1:]
    return s if len(s) == 10 and s.startswith("60") and s[:3] in AREA_CODES_FIJO else None


def _clean_cel_desde_fijo(d):
    s = str(d)
    if s.startswith("0057") and len(s) >= 14: s = s[4:]
    if s.startswith("57")   and len(s) >= 12: s = s[2:]
    return s if len(s) == 10 and s.startswith("3") else None


def _extract_fijo(raw):
    if pd.isna(raw): return pd.NA, pd.NA
    groups = _DIGITS_RE.findall(str(raw))
    fijo = next((f for g in groups for f in [_clean_fijo(g)] if f), pd.NA)
    cel  = next((c for g in groups for c in [_clean_cel_desde_fijo(g)] if c), pd.NA)
    return fijo, cel


def _es_placeholder(s):
    if not (isinstance(s, str) and len(s) == 10 and s.isdigit()): return False
    return bool(re.search(r"(\d)\1{7,}", s)) or (len(set(s)) <= 2 and "0" in s)


def normalizar_telefonos_fijos(df):
    ext = df["Teléfono"].apply(lambda r: pd.Series(_extract_fijo(r)))
    df["Telefono_actualizado"]   = ext[0]
    df["Celular_desde_telefono"] = ext[1]
    mask_bad = df["Telefono_actualizado"].astype("string").apply(_es_placeholder)
    df.loc[mask_bad, "Telefono_actualizado"] = pd.NA
    vc  = df["Telefono_actualizado"].dropna().value_counts()
    gen = set(vc[vc > GENERIC_THRESHOLD].index)
    df["Telefono_actualizado"] = (df["Telefono_actualizado"]
                                  .where(~df["Telefono_actualizado"].isin(gen), "telefono_generico")
                                  .fillna("sin_telefono"))
    df["Celular_desde_telefono"]  = df["Celular_desde_telefono"].fillna("sin_celular")
    df["Categoria_actualizado"]   = np.where(df["Telefono_actualizado"].isin(["telefono_generico","sin_telefono"]),
                                             df["Telefono_actualizado"], "telefono_valido")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUE 5 — FILTRADO Y CONSTRUCCIÓN base_final
# ─────────────────────────────────────────────────────────────────────────────

COLUMNAS_WOLKVOX = [
    "NOMBRE","APELLIDO","TIPOID","ID","EDAD","SEXO","PAIS","DEPARTAMENTO",
    "CIUDAD","ZONA","DIRECCION","OPT1","sds","OPT2","OPT3","OPT4","OPT5",
    "OPT6","OPT7","OPT8","OPT9","OPT10","OPT11","OPT12",
    "TEL1","TEL2","TEL3","TEL4","TEL5","TEL6","TEL7","TEL8","TEL9","TEL10",
    "OTROSTEL","EMAIL","RECALL-INFO","AGENTE","RESULTADOREG","FECHAFINREG",
    "LLAMADAS","IDCALL","COD01","DESC1","COD02","DESC2","COMENTARIOSACUMULADOS",
    "DATE_RECALL","COUNT_RECALL","TEL_RECALL","LAST_DIAL_TEL","HISTORY_TEL",
]


def filtrar_gestionables(df):
    return df[
        (df["Por tiempo de antigüedad"] == "Aplica") &
        (df["Categoria_celular"]        == "celular_valido") &
        (df["Edad < 20 años"]           == "Si Gestiona")
    ].copy()


def construir_base_final(df_g):
    base = pd.DataFrame(index=df_g.index, columns=COLUMNAS_WOLKVOX)
    base["NOMBRE"]   = df_g["Titular"]
    base["APELLIDO"] = df_g["Titular"]
    base["TIPOID"]   = "CC"
    base["ID"]       = df_g["Identificación"].astype(str)
    base["EDAD"]     = df_g["Edad"].astype(str) + " AnOS"
    base["PAIS"]     = ""
    base["OPT1"]     = df_g["Contrato"]
    base["OPT2"]     = df_g["Entidad"]
    base["OPT3"]     = df_g["Producto"]
    base["OPT4"]     = df_g["Plazo"]
    base["OPT5"]     = OPT5_CAMPANA
    base["OPT12"]    = df_g["Celular_actualizado"]
    base["EMAIL"]    = df_g["Correo_Actualizado"]
    telefono = df_g["Telefono_actualizado"]
    celular  = df_g["Celular_actualizado"]
    base["TEL1"] = "'" + np.where(telefono.isin(["sin_telefono","telefono_generico"]),
                                  "96" + celular.astype(str), telefono)
    base["TEL2"] = "'96" + celular.astype(str)
    return base.fillna("")


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUE 6 — ENVÍO DE CORREO CON RESUMEN
# ─────────────────────────────────────────────────────────────────────────────

_LOGO_CID = "logo_olivos"


def _resolver_logo() -> Path | None:
    """Devuelve el Path del logo LogoOlivos_2 si existe, o None."""
    for ext in ("png", "jpg", "jpeg", "PNG", "JPG"):
        p = DIR_BASE / "imagenes" / f"LogoOlivos_2.{ext}"
        if p.exists():
            return p
    return None


def _tabla_html(df: pd.DataFrame, titulo: str) -> str:
    filas = ""
    for i, row in df.iterrows():
        bg = "#f0fafa" if i % 2 == 0 else "#ffffff"
        celdas = ""
        for val in row:
            es_num = isinstance(val, (int, float)) or (isinstance(val, str) and val.endswith("%"))
            align  = "center" if es_num else "left"
            celdas += (
                f'<td style="padding:8px 14px;border-bottom:1px solid #e0eeee;'
                f'font-size:13px;color:#323e45;text-align:{align};">{val}</td>'
            )
        filas += f'<tr style="background:{bg};">{celdas}</tr>'

    def _th_align(col):
        muestra = df[col].dropna().iloc[0] if not df[col].dropna().empty else ""
        es_num  = isinstance(muestra, (int, float)) or (isinstance(muestra, str) and muestra.endswith("%"))
        return "center" if es_num else "left"

    encabezados = "".join(
        f'<th style="background:#5CB8B2;color:#ffffff;padding:9px 14px;'
        f'text-align:{_th_align(c)};font-size:13px;font-weight:600;'
        f'border-bottom:2px solid #2BA390;">{c}</th>'
        for c in df.columns
    )

    return f"""
    <p style="margin:24px 0 6px;font-size:14px;font-weight:700;color:#003B4B;
       border-left:4px solid #FFBF00;padding-left:10px;">{titulo}</p>
    <table style="border-collapse:collapse;width:100%;margin-bottom:8px;
                  font-family:Arial,sans-serif;border-radius:4px;overflow:hidden;">
      <thead><tr>{encabezados}</tr></thead>
      <tbody>{filas}</tbody>
    </table>
    """


def construir_html_resumen(df: pd.DataFrame, base_final: pd.DataFrame) -> str:
    total        = len(df)
    gestionables = len(base_final)
    no_gestion   = total - gestionables
    fecha        = datetime.now().strftime("%d/%m/%Y %H:%M")

    # ── Tarjetas resumen ──────────────────────────────────────────────────────
    def tarjeta(valor, etiqueta, color_fondo, color_texto):
        return (
            f'<td style="width:33%;padding:0 8px;">'
            f'<div style="background:{color_fondo};border-radius:8px;padding:16px 12px;text-align:center;">'
            f'<p style="margin:0;font-size:28px;font-weight:700;color:{color_texto};">{valor:,}</p>'
            f'<p style="margin:4px 0 0;font-size:12px;color:{color_texto};opacity:0.85;">{etiqueta}</p>'
            f'</div></td>'
        )

    # ── Tabla tipología ───────────────────────────────────────────────────────
    tabla_tipologia = pd.DataFrame({
        "Tipología": ["Base Entregada", "Registros a Gestionar", "No Gestionables"],
        "Cantidad" : [total, gestionables, no_gestion],
        "Participación": ["100%", f"{gestionables/total:.1%}", f"{no_gestion/total:.1%}"],
    })

    # ── Tablas de validación ──────────────────────────────────────────────────
    def tabla_cat(col, titulo):
        s = df[col].value_counts().rename_axis("Etiqueta").reset_index(name="Cantidad")
        s["Participación"] = (s["Cantidad"] / total).map("{:.1%}".format)
        return _tabla_html(s, titulo)

    # ── Logo (CID — compatible Gmail) ─────────────────────────────────────────
    logo_path = _resolver_logo()
    logo_html = (
        f'<img src="cid:{_LOGO_CID}" style="height:52px;display:block;" alt="Los Olivos" />'
        if logo_path else
        '<span style="font-size:18px;font-weight:700;color:#FFBF00;letter-spacing:1px;">LOS OLIVOS</span>'
    )

    cuerpo = f"""<!DOCTYPE html>
<html lang="es">
<body style="margin:0;padding:0;background:#edf1f4;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#edf1f4;padding:24px 0;">
<tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;">

  <!-- HEADER -->
  <tr>
    <td style="background:#003B4B;border-radius:10px 10px 0 0;padding:20px 28px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="vertical-align:middle;">{logo_html}</td>
          <td style="vertical-align:middle;text-align:right;">
            <p style="margin:0;font-size:11px;color:#5CB8B2;">Generado automáticamente</p>
            <p style="margin:2px 0 0;font-size:11px;color:#5CB8B2;">{fecha}</p>
          </td>
        </tr>
      </table>
      <p style="margin:14px 0 0;font-size:20px;font-weight:700;color:#FFBF00;
                border-top:1px solid #2BA390;padding-top:14px;">
        Pipeline Contact Center &mdash; Resumen de Validación
      </p>
      <p style="margin:4px 0 0;font-size:13px;color:#a8dbd8;">
        Campaña: <strong style="color:#FFBF00;">{OPT5_CAMPANA}</strong>
      </p>
    </td>
  </tr>

  <!-- INTRODUCCIÓN -->
  <tr>
    <td style="background:#ffffff;padding:22px 28px 0;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="background:#f0fafa;border-left:4px solid #FFBF00;
                     border-radius:0 6px 6px 0;padding:14px 18px;">
            <p style="margin:0 0 4px;font-size:12px;font-weight:700;
                      color:#2BA390;text-transform:uppercase;letter-spacing:0.5px;">
              Objetivo de la campaña
            </p>
            <p style="margin:0;font-size:13px;color:#323e45;line-height:1.6;">
              El objetivo de la campaña es <strong>retener a los clientes del producto corporativo</strong>
              mediante una gestión individualizada, a través de acciones focalizadas que permitan
              <strong>anticipar la desvinculación</strong> y fortalecer la relación con cada cliente.
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- TARJETAS RESUMEN -->
  <tr>
    <td style="background:#ffffff;padding:20px 28px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          {tarjeta(total,        "Base Entregada",         "#003B4B", "#ffffff")}
          {tarjeta(gestionables, "Registros a Gestionar",  "#5CB8B2", "#ffffff")}
          {tarjeta(no_gestion,   "No Gestionables",        "#f0fafa", "#003B4B")}
        </tr>
      </table>
    </td>
  </tr>

  <!-- TABLAS DE VALIDACIÓN -->
  <tr>
    <td style="background:#ffffff;padding:0 28px 24px;">

      {_tabla_html(tabla_tipologia, "Tipología de Registros")}
      {tabla_cat("Categoria_celular",             "Celular")}
      {tabla_cat("Categoria_correo",              "Correo Electrónico")}
      {tabla_cat("Categoria_actualizado",         "Teléfono Fijo")}
      {tabla_cat("Por tiempo de antigüedad",      "Antigüedad")}
      {tabla_cat("Gestionables por tiempo retiro","Gestionables por Tiempo de Retiro")}
      {tabla_cat("Anual",                         "UXS / Plazo")}
      {tabla_cat("Gestion_convenio",              "Gestión por Convenio")}

    </td>
  </tr>

  <!-- FOOTER -->
  <tr>
    <td style="background:#003B4B;border-radius:0 0 10px 10px;padding:14px 28px;text-align:center;">
      <p style="margin:0;font-size:11px;color:#5CB8B2;">
        Archivo exportado: <strong style="color:#ffffff;">base_final.csv</strong>
        &nbsp;·&nbsp; {gestionables:,} registros listos para Wolkvox
      </p>
      <p style="margin:6px 0 0;font-size:10px;color:#2BA390;">
        Los Olivos &copy; {datetime.now().year} &nbsp;·&nbsp; Dirección de Analítica
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body></html>"""
    return cuerpo


def _adjuntar_logo(msg_related: MIMEMultipart) -> None:
    """Adjunta LogoOlivos_2 como imagen inline con CID. No lanza excepción si no existe."""
    logo_path = _resolver_logo()
    if logo_path is None:
        print("⚠️  Logo no encontrado — el correo se enviará sin imagen.")
        return
    try:
        with open(logo_path, "rb") as f:
            imagen = MIMEImage(f.read())
        imagen.add_header("Content-ID", f"<{_LOGO_CID}>")
        imagen.add_header("Content-Disposition", "inline", filename=logo_path.name)
        msg_related.attach(imagen)
    except OSError as e:
        print(f"⚠️  No se pudo adjuntar el logo: {e}")


def enviar_correo_resumen(df: pd.DataFrame, base_final: pd.DataFrame) -> None:
    fecha  = datetime.now().strftime("%d/%m/%Y")
    asunto = f"Contact Center | Resumen de Validación {fecha} — {len(base_final):,} registros gestionables"
    html   = construir_html_resumen(df, base_final)

    # Estructura: related > alternative > html  (permite CID + fallback texto)
    msg_related     = MIMEMultipart("related")
    msg_alternative = MIMEMultipart("alternative")

    todos_destinatarios = EMAIL_DESTINO + EMAIL_COPIA

    msg_related["Subject"] = asunto
    msg_related["From"]    = SMTP_USER
    msg_related["To"]      = ", ".join(EMAIL_DESTINO)
    if EMAIL_COPIA:
        msg_related["Cc"]  = ", ".join(EMAIL_COPIA)

    msg_alternative.attach(MIMEText(html, "html", "utf-8"))
    msg_related.attach(msg_alternative)

    # Adjuntar logo inline
    _adjuntar_logo(msg_related)

    # Envío con manejo detallado de errores
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as servidor:
            servidor.ehlo()
            servidor.starttls()
            servidor.login(SMTP_USER, SMTP_PASSWORD)
            servidor.sendmail(SMTP_USER, todos_destinatarios, msg_related.as_bytes())
        cc_info = f" | CC: {', '.join(EMAIL_COPIA)}" if EMAIL_COPIA else ""
        print(f"✅ Correo enviado a {EMAIL_DESTINO}{cc_info}")

    except smtplib.SMTPAuthenticationError:
        print("❌ Correo: error de autenticación — verificar usuario/contraseña de aplicación.")
    except smtplib.SMTPConnectError:
        print(f"❌ Correo: no se pudo conectar a {SMTP_SERVER}:{SMTP_PORT}.")
    except smtplib.SMTPRecipientsRefused:
        print(f"❌ Correo: destinatario rechazado ({EMAIL_DESTINO}).")
    except smtplib.SMTPException as e:
        print(f"❌ Correo: error SMTP — {e}")
    except OSError as e:
        print(f"❌ Correo: error de red — {e}")


def enviar_log_ejecucion(log_path: Path, estado: str = "EXITOSO") -> None:
    """Envía el log de tiempos en un correo separado."""
    from email.mime.base import MIMEBase
    from email import encoders as _encoders

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"[LOG] Pipeline Call Center — {estado} {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        msg["From"]    = SMTP_USER
        msg["To"]      = ", ".join(EMAIL_LOG)

        cuerpo = (
            f"Log de ejecución adjunto.\n\n"
            f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Campaña: {OPT5_CAMPANA}\n"
            f"Estado: {estado}\n"
        )
        msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

        if Path(log_path).exists():
            with open(log_path, "rb") as f:
                parte = MIMEBase("application", "octet-stream")
                parte.set_payload(f.read())
            _encoders.encode_base64(parte)
            parte.add_header("Content-Disposition", f'attachment; filename="{Path(log_path).name}"')
            msg.attach(parte)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as servidor:
            servidor.ehlo()
            servidor.starttls()
            servidor.login(SMTP_USER, SMTP_PASSWORD)
            servidor.sendmail(SMTP_USER, EMAIL_LOG, msg.as_string())
        print(f"📋 Log enviado por correo: {Path(log_path).name}")
    except Exception as e:
        print(f"⚠️  No se pudo enviar log por correo: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUE 7 — CARGA A WOLKVOX API
# ─────────────────────────────────────────────────────────────────────────────

_WV_HEADERS = {
    "wolkvox_server": WV_SERVER,
    "wolkvox-token":  WV_TOKEN,
    "Content-Type":   "application/json",
}


def crear_campana_wolkvox() -> str:
    """Crea una nueva campaña preview en Wolkvox y retorna su ID."""
    print("--------------------------------------------------")
    print(" WOLKVOX | Creando campaña")
    print("--------------------------------------------------")

    url = (
        f"https://{WV_SERVER}.wolkvox.com/api/v2/campaign.php"
        f"?api=create_campaign&type_campaign={WV_CAMPAIGN_TYPE}"
    )
    payload = {
        "campaign_name":        f"UXS_Automatica_{_MESES_ES[_mes_ant_num]}_{_anio_ant}",
        "campaign_description": f"UXS_Automatica_{_MESES_ES[_mes_ant_num]}_{_anio_ant}",
        "start_time":           WV_HORA_INICIO,
        "end_time":             WV_HORA_FIN,
        "skill_id":             WV_SKILL_ID,
        "enable_edition":       "no",
    }

    resp  = requests.post(url, headers=_WV_HEADERS, json=payload, timeout=30)
    datos = resp.json()

    if resp.status_code in [200, 201] and str(datos.get("code")) in ["200", "201"]:
        campaign_id = str(datos["data"][0]["id_campaign"])
        print(f"✅ Campaña creada: {payload['campaign_name']} | ID: {campaign_id}\n")
        return campaign_id

    raise RuntimeError(f"Error al crear campaña Wolkvox: {resp.text}")


def cargar_a_wolkvox(base_final: pd.DataFrame, campaign_id: str) -> None:
    """Carga base_final a la campaña Wolkvox en lotes de WV_BATCH_SIZE."""
    print("--------------------------------------------------")
    print(f" WOLKVOX | Cargando registros → campaña {campaign_id}")
    print("--------------------------------------------------")

    url = (
        f"https://{WV_SERVER}.wolkvox.com/api/v2/campaign.php"
        f"?api=add_record&type_campaign={WV_CAMPAIGN_TYPE}&campaign_id={campaign_id}"
    )

    total    = len(base_final)
    errores  = 0

    for i in range(0, total, WV_BATCH_SIZE):
        bloque  = base_final.iloc[i : i + WV_BATCH_SIZE]
        n_lote  = i // WV_BATCH_SIZE + 1
        payload = []

        for _, row in bloque.iterrows():
            payload.append({
                "customer_name":      str(row["NOMBRE"]),
                "customer_last_name": str(row["APELLIDO"]),
                "id_type":            "",
                "customer_id":        str(row["ID"]),
                "age":                str(row.get("EDAD", "")),
                "gender":             str(row.get("SEXO", "")),
                "country":            "",
                "state":              "",
                "city":               str(row.get("CIUDAD", "")),
                "zone":               str(row.get("ZONA", "")),
                "address":            str(row.get("DIRECCION", "")),
                "opt1":               str(row["OPT1"]),
                "opt2":               str(row["OPT2"]),
                "opt3":               str(row["OPT3"]),
                "opt4":               str(row["OPT4"]),
                "opt5":               str(row["OPT5"]),
                "opt6":  "", "opt7":  "", "opt8":  "", "opt9":  "",
                "opt10": "", "opt11": "", "opt12": str(row["OPT12"]),
                "tel1":  str(row["TEL1"]).lstrip("'"),
                "tel2":  str(row["TEL2"]).lstrip("'"),
                "tel3":  "", "tel4":  "", "tel5":  "",
                "tel6":  "", "tel7":  "", "tel8":  "", "tel9":  "", "tel10": "",
                "agent_id": "",
            })

        try:
            resp = requests.post(url, headers=_WV_HEADERS, json=payload, timeout=60)
            procesados = min(i + WV_BATCH_SIZE, total)
            if resp.status_code in [200, 201]:
                print(f"  ✅ Lote {n_lote:>3} | {procesados}/{total} registros")
            else:
                print(f"  ❌ Lote {n_lote:>3} | [{resp.status_code}] {resp.text[:150]}")
                errores += 1
        except requests.RequestException as e:
            print(f"  ⚠️  Lote {n_lote:>3} | Error de conexión: {e}")
            errores += 1

        time.sleep(0.5)

    total_lotes = -(-total // WV_BATCH_SIZE)
    print(f"\n🎉 Carga completada | Lotes con error: {errores}/{total_lotes}\n")


# ─────────────────────────────────────────────────────────────────────────────
# EJECUCIÓN PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def ejecutar_pipeline() -> pd.DataFrame:
    LOG_DIR.mkdir(exist_ok=True)

    tiempos = {}
    t_total = time.time()

    def lap(nombre: str, t_inicio: float) -> float:
        seg = time.time() - t_inicio
        tiempos[nombre] = seg
        print(f"  ⏱  {nombre:<40} {seg:>6.1f} seg")
        return time.time()

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  INICIO PIPELINE  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"  Campaña: {OPT5_CAMPANA}")
    print(f"{sep}")

    # 1. Autenticación
    t = time.time()
    token = obtener_token()
    if not token:
        raise RuntimeError("No se pudo obtener token.")
    t = lap("1. Autenticación API", t)

    # 2. Carga de datos
    retiros   = cargar_retiros()
    contratos = cargar_contratos_api(token)
    pyg       = cargar_pyg()
    gerencia  = cargar_gerencia_api(token)
    t = lap("2. Carga de datos (API + CSVs)", t)

    # 3. Enriquecimiento
    retiros = enriquecer_con_contratos(retiros, contratos)
    retiros = enriquecer_con_pyg(retiros, pyg)
    retiros = calcular_antiguedad_y_reglas(retiros)
    retiros = marcar_gestion_convenio(retiros, gerencia)
    t = lap("3. Enriquecimiento y reglas", t)

    # 4. Normalización de contactos
    retiros = normalizar_correos(retiros)
    retiros = normalizar_celulares(retiros)
    retiros = normalizar_telefonos_fijos(retiros)
    t = lap("4. Normalización de contactos", t)

    # 5. Filtrado y construcción
    gestionables = filtrar_gestionables(retiros)
    base_final   = construir_base_final(gestionables)
    t = lap("5. Filtrado + base_final", t)

    # 6. Exportar CSV — historial organizado en historico/
    HISTORICO_DIR = DIR_BASE / "historico"
    HISTORICO_DIR.mkdir(exist_ok=True)
    ts_csv     = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = HISTORICO_DIR / f"base_final_{ts_csv}.csv"
    base_final.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n✅ base_final → {output_path}  ({len(base_final)} registros)")
    t = lap("6. Exportar CSV", t)

    # 7. Crear campaña y cargar registros a Wolkvox
    campaign_id = crear_campana_wolkvox()
    cargar_a_wolkvox(base_final, campaign_id)
    t = lap("7. Carga a Wolkvox", t)

    # ── Resumen de tiempos ────────────────────────────────────────────────────
    total_seg = time.time() - t_total
    print(f"\n{sep}")
    print(f"  RESUMEN DE TIEMPOS")
    print(f"{sep}")
    for paso, seg in tiempos.items():
        pct = seg / total_seg * 100
        barra = "█" * int(pct / 5)
        print(f"  {paso:<40} {seg:>6.1f} seg  {pct:>5.1f}%  {barra}")
    print(f"  {'─'*58}")
    print(f"  {'TOTAL':<40} {total_seg:>6.1f} seg  ({total_seg/60:.1f} min)")
    print(f"{sep}\n")

    # ── Guardar log de tiempos ────────────────────────────────────────────────
    log_path = LOG_DIR / f"tiempos_{datetime.now().strftime('%Y%m%d_%H%M')}.log"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"Pipeline Contact Center — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
        f.write(f"Campaña: {OPT5_CAMPANA}\n")
        f.write(f"Estado: EXITOSO\n")
        f.write(f"Registros gestionables: {len(base_final)}\n")
        f.write(f"CSV generado: {output_path.name}\n\n")
        for paso, seg in tiempos.items():
            f.write(f"{paso:<40} {seg:>6.1f} seg\n")
        f.write(f"{'─'*50}\n")
        f.write(f"{'TOTAL':<40} {total_seg:>6.1f} seg  ({total_seg/60:.1f} min)\n")
    print(f"📋 Log guardado: {log_path}")

    # 8. Enviar correos: resumen estadístico y log por separado
    enviar_correo_resumen(retiros, base_final)
    enviar_log_ejecucion(log_path, estado="EXITOSO")
    lap("8. Envío de correos", t)

    return base_final


def enviar_correo_error(traceback_str: str, log_path: Path = None) -> None:
    """Envía correo de alerta cuando el pipeline falla con el traceback y log adjunto."""
    from email.mime.base import MIMEBase
    from email import encoders as _encoders

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"[ALERTA] Pipeline Call Center — Error {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        msg["From"]    = SMTP_USER
        msg["To"]      = ", ".join(EMAIL_LOG)

        cuerpo = (
            f"Se detectó un error en la ejecución automática del Pipeline Call Center.\n\n"
            f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Campaña: {OPT5_CAMPANA}\n\n"
            f"{'─' * 60}\n"
            f"DETALLE DEL ERROR\n"
            f"{'─' * 60}\n"
            f"{traceback_str}"
        )
        msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

        # Adjuntar log de error si existe
        if log_path and Path(log_path).exists():
            with open(log_path, "rb") as f:
                parte = MIMEBase("application", "octet-stream")
                parte.set_payload(f.read())
            _encoders.encode_base64(parte)
            parte.add_header("Content-Disposition", f'attachment; filename="{Path(log_path).name}"')
            msg.attach(parte)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as servidor:
            servidor.starttls()
            servidor.login(SMTP_USER, SMTP_PASSWORD)
            servidor.sendmail(SMTP_USER, EMAIL_LOG, msg.as_string())
        print("📧 Correo de error enviado.")
    except Exception as e:
        print(f"⚠️  No se pudo enviar correo de error: {e}")


if __name__ == "__main__":
    import traceback as _tb
    try:
        ejecutar_pipeline()
    except Exception:
        error_detalle = _tb.format_exc()
        print(f"\n❌ PIPELINE FALLIDO:\n{error_detalle}")

        # Guardar log parcial de error
        LOG_DIR.mkdir(exist_ok=True)
        log_error_path = LOG_DIR / f"tiempos_{datetime.now().strftime('%Y%m%d_%H%M')}.log"
        with open(log_error_path, "w", encoding="utf-8") as f:
            f.write(f"Pipeline Contact Center — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            f.write(f"Campaña: {OPT5_CAMPANA}\n")
            f.write(f"Estado: FALLIDO\n\n")
            f.write(f"{'─'*50}\n")
            f.write(f"DETALLE DEL ERROR\n")
            f.write(f"{'─'*50}\n")
            f.write(error_detalle)
        print(f"📋 Log de error guardado: {log_error_path}")

        enviar_correo_error(error_detalle, log_error_path)
        raise SystemExit(1)
