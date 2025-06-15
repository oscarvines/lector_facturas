import os
import re
import pandas as pd
from google.cloud import documentai_v1 as documentai
from google.cloud import storage

# --- CONFIGURACIÓN ---
PROJECT_ID   = "772723410003"
LOCATION     = "us"
PROCESSOR_ID = "dff8117c158462cd"
BUCKET_NAME  = "facturasclientes"
OUTPUT_DIR   = "output_docai"
ERROR_LOG    = "errores_procesamiento.csv"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Inicializar clientes
docai_client   = documentai.DocumentProcessorServiceClient()
processor_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"
storage_client = storage.Client()
bucket         = storage_client.bucket(BUCKET_NAME)

# --- FUNCIONES AUXILIARES ---

def parse_float_es(valor: str) -> float:
    """Convierte '1.234,56' a float 1234.56; si falla, devuelve 0.0"""
    if not valor:
        return 0.0
    limpia = re.sub(r"[^\d,\.]", "", valor).replace('.', '').replace(',', '.')
    try:
        return float(limpia)
    except ValueError:
        return 0.0


def procesar_factura(blob) -> dict:
    """Procesa una factura PDF y devuelve un dict con los campos solicitados."""
    content = blob.download_as_bytes()
    if not content:
        raise ValueError("El archivo está vacío o corrupto")

    raw_doc = documentai.RawDocument(content=content, mime_type="application/pdf")
    req = documentai.ProcessRequest(name=processor_name, raw_document=raw_doc)
    res = docai_client.process_document(request=req)
    doc = res.document

    # Inicializar campos
    datos = {
        "Archivo": blob.name,
        "Proveedor": "",
        "Dirección": "",
        "Teléfono": "",
        "Nº Factura": "",
        "Fecha Emisión": "",
        "Nº Pedido": "",
        "Base Imponible": "",
        "IVA": "",
        "Importe Total": "",
        "CIF Proveedor": "",
        "Concepto": []
    }

    # Extraer entidades según tipos deseados
    for e in doc.entities:
        text = e.mention_text or ""
        t = e.type_
        if t == "supplier_name":
            datos["Proveedor"] = text
        elif t == "supplier_address":
            datos["Dirección"] = text
        elif t == "supplier_phone":
            datos["Teléfono"] = text
        elif t == "supplier_tax_id":
            datos["CIF Proveedor"] = text
        elif t == "invoice_id":
            datos["Nº Factura"] = text
        elif t == "invoice_date":
            datos["Fecha Emisión"] = text
        elif t == "purchase_order":
            datos["Nº Pedido"] = text
        elif t == "net_amount":
            valor = parse_float_es(text)
            datos["Base Imponible"] = f"{valor:.2f}".replace('.', ',')
        elif t == "total_tax_amount":
            valor = parse_float_es(text)
            datos["IVA"] = f"{valor:.2f}".replace('.', ',')
        elif t == "total_amount":
            datos["Importe Total"] = text
        elif t == "line_item":
            for p in e.properties:
                if p.type_.endswith("description"):
                    datos["Concepto"].append(p.mention_text or "")

    # Unir conceptos en una sola celda
    datos["Concepto"] = " | ".join(filter(None, datos["Concepto"]))

    return datos


def guardar_excel(cliente: str, proyecto: str, filas: list[dict]):
    nombre = f"{cliente}_{proyecto}.xlsx"
    ruta   = os.path.join(OUTPUT_DIR, nombre)

    # Crear DataFrame y reordenar columnas para que 'CIF Proveedor' sea la segunda
    df_nuevo = pd.DataFrame(filas)
    columnas_orden = [
        "Archivo",
        "CIF Proveedor",
        "Proveedor",
        "Dirección",
        "Teléfono",
        "Nº Factura",
        "Fecha Emisión",
        "Nº Pedido",
        "Base Imponible",
        "IVA",
        "Importe Total",
        "Concepto"
    ]
    # Asegurar que todas las columnas existen en el DataFrame
    columnas_final = [col for col in columnas_orden if col in df_nuevo.columns]
    df_nuevo = df_nuevo[columnas_final]

    if os.path.exists(ruta):
        df_exist = pd.read_excel(ruta)
        # Reordenar también el existente antes de concatenar
        df_exist = df_exist[columnas_final]
        df = pd.concat([df_exist, df_nuevo], ignore_index=True)
    else:
        df = df_nuevo

    df.to_excel(ruta, index=False)
    print(f"✅ Guardado/actualizado: {ruta}")


def seleccionar_opcion(lista, titulo):
    print(f"\n📂 {titulo}")
    for i, v in enumerate(lista, 1):
        print(f" {i}. {v}")
    while True:
        try:
            sel = int(input("> ")) - 1
            return lista[sel]
        except Exception:
            print("Opción inválida, inténtalo de nuevo.")


def main_interactivo():
    print("🧾 Procesador de facturas con Document AI")

    blobs = list(bucket.list_blobs())
    clientes = set()
    proyectos = {}
    for blob in blobs:
        if blob.name.lower().endswith(".pdf") and "/" in blob.name:
            c,p = blob.name.split("/",1)
            clientes.add(c)
            proyectos.setdefault(c,set()).add(p.split("/",1)[0])

    clientes = sorted(clientes)
    proyectos = {c: sorted(proyectos[c]) for c in clientes}

    cliente  = seleccionar_opcion(clientes, "¿Qué cliente procesar?")
    proyecto = seleccionar_opcion(proyectos[cliente], f"¿Qué proyecto de {cliente}?            ")

    filas, errores = [], []
    for blob in blobs:
        if not blob.name.startswith(f"{cliente}/{proyecto}/"):
            continue
        try:
            if blob.size == 0:
                raise ValueError("Archivo vacío")
            print(f"Procesando {blob.name}...")
            datos = procesar_factura(blob)
            filas.append(datos)
        except Exception as e:
            errores.append({"Archivo": blob.name, "Error": str(e)})

    if filas:
        guardar_excel(cliente, proyecto, filas)
    if errores:
        pd.DataFrame(errores).to_csv(os.path.join(OUTPUT_DIR, ERROR_LOG), index=False)
        print(f"⚠️ Errores registrados en {ERROR_LOG}")
    print("✅ Proceso completado.")

if __name__ == "__main__":
    main_interactivo()
