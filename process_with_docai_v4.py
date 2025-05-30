import os
import re
from google.cloud import documentai_v1 as documentai
from google.cloud import storage
import pandas as pd

# --- CONFIGURACIÓN ---
PROJECT_ID = "772723410003"           # Reemplaza con tu ID de proyecto
LOCATION = "us"                       # Zona donde esté tu Processor
PROCESSOR_ID = "dff8117c158462cd"     # ID de tu Invoice Processor
BUCKET_NAME = "facturasclientes"      # Nombre del bucket con los PDFs
OUTPUT_DIR = "output_docai_v3"        # Carpeta local para los Excel

docai_client = documentai.DocumentProcessorServiceClient()
name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- FUNCIONES AUXILIARES ---

def buscar_en_texto(texto, patron):
    """
    Retorna la primera coincidencia del regex 'patron' en 'texto'.
    Si no se encuentra, retorna cadena vacía.
    """
    match = re.search(patron, texto, re.IGNORECASE)
    return match.group(1).strip() if match else ""

def parse_float_es(valor):
    """
    Convierte una cadena con formato español (1.234,56) a float (1234.56).
    Si no se puede convertir, retorna 0.0
    """
    if not valor:
        return 0.0
    # Quitar puntos de miles y cambiar coma decimal por punto
    valor = valor.replace('.', '').replace(',', '.')
    try:
        return float(valor)
    except:
        return 0.0

def extraer_del_texto_libre(texto):
    """
    Fallback para extraer Base imponible, IVA y Concepto
    cuando no vengan como entidades del Invoice Processor
    ni line items. Ajusta patrones según tu formato.
    """
    base = buscar_en_texto(texto, r"base\s+imponible\s*[^\d]*(\d+[.,]\d+)")
    iva = buscar_en_texto(texto, r"(?:iva\s*\(?\d+%?\)?:?|i\s*v\s*a|i\.v\.a\.|i\.v\.a)\s*([0-9.,]+)")

    # Busca la palabra "CONCEPTO" o "DESCRIPCIÓN", y coge lo que haya hasta "BASE IMPONIBLE"
    concepto = ""
    texto_upper = texto.upper()
    if "CONCEPTO" in texto_upper:
        concepto_split = texto_upper.split("CONCEPTO", 1)[-1]
        if "BASE IMPONIBLE" in concepto_split:
            concepto = concepto_split.split("BASE IMPONIBLE")[0].strip()
        else:
            concepto = concepto_split.strip()
    elif "DESCRIPCIÓN" in texto_upper:
        desc_split = texto_upper.split("DESCRIPCIÓN", 1)[-1]
        if "BASE IMPONIBLE" in desc_split:
            concepto = desc_split.split("BASE IMPONIBLE")[0].strip()
        else:
            concepto = desc_split.strip()

    return base, iva, concepto

def procesar_factura(blob):
    content = blob.download_as_bytes()
    raw_document = documentai.RawDocument(content=content, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    result = docai_client.process_document(request=request)
    doc = result.document

    supplier = ""
    cif_supplier = ""
    customer = ""
    cif_customer = ""
    invoice_date = ""
    invoice_id = ""
    base_global = ""
    iva_global = ""
    total_global = ""

    line_items = []

    # 1) Recorremos TODAS las entidades
    for e in doc.entities:
        if e.type_ == "supplier_name":
            supplier = e.mention_text
        elif e.type_ == "supplier_tax_id":
            cif_supplier = e.mention_text
        elif e.type_ == "customer_name":
            customer = e.mention_text
        elif e.type_ == "customer_tax_id":
            cif_customer = e.mention_text
        elif e.type_ == "invoice_date":
            invoice_date = e.mention_text
        elif e.type_ == "invoice_id":
            invoice_id = e.mention_text
        elif e.type_ == "total_amount":
            total_global = e.mention_text

        elif e.type_ == "line_item":
            line_items.append(e)

        # --- CLAVE: Capturar 'vat' / 'net_amount' si aparece ---
        elif e.type_ == "vat":
            # Generalmente 'vat/amount' = base, 'vat/tax_amount' = IVA
            for prop in e.properties:
                if prop.type_ == "vat/amount":
                    base_global = prop.mention_text
                elif prop.type_ == "vat/tax_amount":
                    iva_global = prop.mention_text

        elif e.type_ == "net_amount":
            # A veces 'net_amount' es realmente la base imponible
            # Si Document AI no devuelve 'vat', podrías usar net_amount como base
            if not base_global:
                base_global = e.mention_text

    # 2) Leer line items y unir sus descripciones (si quieres un solo concepto)
    descripciones = []
    for line_item in line_items:
        # Para cada line_item, leemos sus properties
        desc = []
        for prop in line_item.properties:
            if prop.type_ == "line_item/description":
                desc.append(prop.mention_text)

        if desc:
            descripciones.append(" ".join(desc))

    concepto_unico = " | ".join(descripciones).strip()

    # 3) Si NO hay 'vat' y 'net_amount', fallback a OCR
    if not base_global and not iva_global:
        texto_ocr = doc.text
        base_fbk, iva_fbk, c_fbk = extraer_del_texto_libre(texto_ocr)
        if base_fbk:
            base_global = base_fbk
        if iva_fbk:
            iva_global = iva_fbk
        if not concepto_unico and c_fbk:
            concepto_unico = c_fbk

    # 4) Construimos la fila
    fila = {
        "Archivo": blob.name,
        "Proveedor": supplier,
        "CIF_Proveedor": cif_supplier,
        "Cliente": customer,
        "CIF_Cliente": cif_customer,
        "Fecha": invoice_date,
        "Nº Factura": invoice_id,
        "Base Imponible": base_global,  # <- se rellena desde e.type_=="vat" > vat/amount
        "IVA": iva_global,             # <- se rellena desde e.type_=="vat" > vat/tax_amount
        "Total": total_global,         # <- se rellena desde e.type_=="total_amount"
        "Concepto": concepto_unico
    }

    return [fila]

def guardar_excel(cliente, proyecto, filas):
    """
    Guarda o actualiza un Excel por cliente y proyecto.
    'filas' es la lista de diccionarios (normalmente 1 dict por factura).
    """
    nombre_excel = f"{cliente}_{proyecto}.xlsx"
    ruta = os.path.join(OUTPUT_DIR, nombre_excel)

    df_nuevo = pd.DataFrame(filas)

    if os.path.exists(ruta):
        df_existente = pd.read_excel(ruta)
        df_final = pd.concat([df_existente, df_nuevo], ignore_index=True)
    else:
        df_final = df_nuevo

    df_final.to_excel(ruta, index=False)
    print(f"Guardado/actualizado: {ruta}")


def obtener_cliente_proyecto(blob_name):
    """
    Dado el nombre del blob, extrae la carpeta [0] como 'cliente'
    y la carpeta [1] como 'proyecto' (si existe).
    Ajusta si tu estructura difiere.
    """
    partes = blob_name.split("/")
    cliente = partes[0] if len(partes) > 0 else "Desconocido"
    proyecto = partes[1] if len(partes) > 1 else "General"
    return cliente, proyecto

import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="Procesador de facturas con Document AI")
    parser.add_argument("--cliente", help="Nombre del cliente (carpeta raíz)")
    parser.add_argument("--proyecto", help="Nombre del proyecto (subcarpeta)")
    return parser.parse_args()
def main():
    args = parse_args()
    blobs = bucket.list_blobs()

    for blob in blobs:
        if not blob.name.lower().endswith(".pdf"):
            continue

        cliente, proyecto = obtener_cliente_proyecto(blob.name)

        # Filtrar si se han pasado cliente y/o proyecto
        if args.cliente and cliente != args.cliente:
            continue
        if args.proyecto and proyecto != args.proyecto:
            continue

        print(f"Procesando {blob.name}...")
        filas_factura = procesar_factura(blob)
        guardar_excel(cliente, proyecto, filas_factura)

if __name__ == "__main__":
    main()



