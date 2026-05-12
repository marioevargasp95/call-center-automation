"""
test_wolkvox_carga.py
=====================
Script de prueba para verificar la creación de campaña
y carga de registros en Wolkvox antes de correr el pipeline completo.
"""

import requests
import sys

# ── Configuración Wolkvox ──────────────────────────────────────────────────
WV_SERVER        = "xxxxxxxxx"
WV_TOKEN         = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
WV_SKILL_ID      = "4111"
WV_CAMPAIGN_TYPE = "preview"
WV_HORA_INICIO   = "080000"
WV_HORA_FIN      = "180000"

HEADERS = {
    "wolkvox_server": WV_SERVER,
    "wolkvox-token":  WV_TOKEN,
    "Content-Type":   "application/json",
}

# ── Registro de prueba ─────────────────────────────────────────────────────
REGISTRO_PRUEBA = {
    "customer_name":      "Juan",
    "customer_last_name": "Juan",
    "id_type":            "CC",
    "customer_id":        "1000000000",
    "age":                "",
    "gender":             "",
    "country":            "COL",
    "state":              "",
    "city":               "",
    "zone":               "",
    "address":            "",
    "opt1":               "TEST-001",
    "opt2":               "Entidad prueba",
    "opt3":               "Producto prueba",
    "opt4":               "Plazo prueba",
    "opt5":               "UXS_TEST",
    "opt6":  "", "opt7":  "", "opt8":  "", "opt9":  "",
    "opt10": "", "opt11": "", "opt12": "",
    "tel1":  "3001234567",
    "tel2":  "", "tel3":  "", "tel4":  "", "tel5":  "",
    "tel6":  "", "tel7":  "", "tel8":  "", "tel9":  "", "tel10": "",
    "agent_id": "",
}

# ══════════════════════════════════════════════════════════════════════════════
sep = "=" * 55

# PASO 1 — Crear campaña de prueba
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{sep}")
print("  PASO 1 | Creando campaña de prueba en Wolkvox")
print(f"{sep}")

url_crear = (
    f"https://{WV_SERVER}.wolkvox.com/api/v2/campaign.php"
    f"?api=create_campaign&type_campaign={WV_CAMPAIGN_TYPE}"
)
payload_campana = {
    "campaign_name":        "TEST_Pipeline_Prueba",
    "campaign_description": "Campaña de prueba — se puede eliminar",
    "start_time":           WV_HORA_INICIO,
    "end_time":             WV_HORA_FIN,
    "skill_id":             WV_SKILL_ID,
    "enable_edition":       "no",
}

try:
    resp = requests.post(url_crear, headers=HEADERS, json=payload_campana, timeout=30)
    print(f"HTTP Status : {resp.status_code}")
    print(f"Respuesta   : {resp.text}\n")

    datos = resp.json()
    if resp.status_code in [200, 201] and str(datos.get("code")) in ["200", "201"]:
        campaign_id = str(datos["data"][0]["id_campaign"])
        print(f"✅ Campaña creada correctamente")
        print(f"   Nombre : TEST_Pipeline_Prueba")
        print(f"   ID     : {campaign_id}\n")
    else:
        print("❌ No se pudo crear la campaña — revisa el token o el skill_id")
        sys.exit(1)

except Exception as e:
    print(f"❌ Error de conexión: {e}")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# PASO 2 — Cargar registro de prueba
# ══════════════════════════════════════════════════════════════════════════════
print(f"{sep}")
print(f"  PASO 2 | Cargando registro de prueba → campaña {campaign_id}")
print(f"{sep}")
print(f"  Nombre   : {REGISTRO_PRUEBA['customer_name']}")
print(f"  Cédula   : {REGISTRO_PRUEBA['customer_id']}")
print(f"  Teléfono : {REGISTRO_PRUEBA['tel1']}\n")

url_cargar = (
    f"https://{WV_SERVER}.wolkvox.com/api/v2/campaign.php"
    f"?api=add_record&type_campaign={WV_CAMPAIGN_TYPE}&campaign_id={campaign_id}"
)

try:
    resp2 = requests.post(url_cargar, headers=HEADERS, json=[REGISTRO_PRUEBA], timeout=30)
    print(f"HTTP Status : {resp2.status_code}")
    print(f"Respuesta   : {resp2.text}\n")

    if resp2.status_code in [200, 201]:
        print("✅ Registro cargado correctamente")
        print(f"   Verifica en Wolkvox Manager → campaña ID {campaign_id}")
    else:
        print("❌ Error al cargar el registro")

except Exception as e:
    print(f"❌ Error de conexión: {e}")

print(f"\n{sep}\n")
