import os
import re
import argparse
import pandas as pd
from google.cloud import documentai_v1 as documentai
from google.cloud import storage

# --- CONFIGURACIÓN ---
PROJECT_ID = "772723410003"
LOCATION = "us"
PROCESSOR_ID = "dff8117c158462cd"
BUCKET_NAME = "facturasclientes"
OUTPUT_DIR = "output_docai"

os.makedirs(OUTPUT_DIR, exist_ok=True)

docai_client = documentai.DocumentProcessorServiceClient()
processor_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

# ---------------- UTILIDADES ----------------

def parse_amount(valor: str) -> float:
    """
    Convierte importes en formato ES / EN a float.
    Evita errores tipo IVA = 1 por mal parseo.
    """
    if not valor:
        return 0.0

    s = re.sub(r"[^\d,.\-]", "", valor)
    if not s:
        return 0.0

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # 1.234,56 -> 1234.56
            s = s.replace(".", "").replace(",", ".")
        else:
            # 1,234.56 -> 1234.56
            s = s.replace(",", "")
    else:
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        # solo punto -> no tocar

    try:
        return float(s)
    except ValueError:
        return 0.0


def buscar_en_texto(texto, patron):
    match = re.search(patron, texto, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def extraer_del_texto_libre(texto):
    base = buscar_en_texto(texto, r"base\s+(?:imponible|del iva)\s*[^\d]*(\d+[.,]\d+)")
    iva = buscar_en_texto(texto, r"(?:iva\s*\(?\d+%?\)?)\s*([0-9.,]+)")

    concepto = ""
    texto_upper = texto.upper()
    if "CONCEPTO" in texto_upper:
        concepto = texto_upper.split("CONCEPTO", 1)[-1]
    elif "DESCRIPCIÓN" in texto_upper:
        concepto = texto_upper.split("DESCRIPCIÓN", 1)[-1]

    concepto = concepto.split("BASE")[0].strip()
    return base, iva, concepto


# ---------------- PROCESAMIENTO PRINCIPAL ----------------

def procesar_factura(blob):
    content = blob.download_as_bytes()
    raw_document = documentai.RawDocument(content=content, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=processor_name, raw_document=raw_document)
    result = docai_client.process_document(request=request)
    doc = result.document

    supplier = ""
    cif_supplier = ""
    customer = ""
    cif_customer = ""
    invoice_date = ""
    invoice_id = ""
    total_global = ""

    base_candidates = []
    iva_candidates = []
    line_items = []

    # --- 1) Recorremos entidades ---
    for e in doc.entities:
        t = e.type_
        text = e.mention_text or ""

        if t == "supplier_name":
            supplier = text
        elif t == "supplier_tax_id":
            cif_supplier = text
        elif t == "customer_name":
            customer = text
        elif t == "customer_tax_id":
            cif_customer = text
        elif t == "invoice_date":
            invoice_date = text
        elif t == "invoice_id":
            invoice_id = text
        elif t == "total_amount":
            total_global = text

        elif t == "vat":
            for prop in e.properties:
                if prop.type_ == "vat/amount":
                    base_candidates.append(prop.mention_text)
                elif prop.type_ == "vat/tax_amount":
                    iva_candidates.append(prop.mention_text)

        elif t == "net_amount":
            base_candidates.append(text)

        elif t == "total_tax_amount":
            iva_candidates.append(text)

        elif t == "line_item":
            line_items.append(e)

    # --- 2) Resolver BASE e IVA ---
    base_valores = [parse_amount(v) for v in base_candidates if parse_amount(v) > 0]
    iva_valores  = [parse_amount(v) for v in iva_candidates if parse_amount(v) > 0]

    base_global = max(base_valores) if base_valores else ""
    iva_global  = max(iva_valores) if iva_valores else ""

    # --- 3) Concepto ---
    descripciones = []
    for li in line_items:
        for p in li.properties:
            if p.type_ == "line_item/description":
                descripciones.append(p.mention_text)

    concepto_unico = " | ".join(descripciones).strip()

    # --- 4) Fallback OCR ---
    if not base_global or not iva_global:
        texto_ocr = doc.text
        base_fbk, iva_fbk, c_fbk = extraer_del_texto_libre(texto_ocr)
        if not base_global and base_fbk:
            base_global = parse_amount(base_fbk)
        if not iva_global and iva_fbk:
            iva_global = parse_amount(iva_fbk)
        if not concepto_unico and c_fbk:
            concepto_unico = c_fbk

    # --- 5) Corrección Supplier / Cliente ---
    if cif_supplier and cif_customer and cif_supplier == cif_customer:
        supplier, customer = customer, supplier
        cif_supplier, cif_customer = cif_customer, cif_supplier

    return [{
        "Archivo": blob.name,
        "Proveedor": supplier,
        "CIF_Proveedor": cif_supplier,
        "Cliente": customer,
        "CIF_Cliente": cif_customer,
        "Fecha": invoice_date,
        "Nº Factura": invoice_id,
        "Base Imponible": base_global,
        "IVA": iva_global,
        "Total": total_global,
        "Concepto": concepto_unico
    }]


# ---------------- IO ----------------

def guardar_excel(cliente, proyecto, filas):
    ruta = os.path.join(OUTPUT_DIR, f"{cliente}_{proyecto}.xlsx")
    df_nuevo = pd.DataFrame(filas)

    if os.path.exists(ruta):
        df_existente = pd.read_excel(ruta)
        df_final = pd.concat([df_existente, df_nuevo], ignore_index=True)
    else:
        df_final = df_nuevo

    df_final.to_excel(ruta, index=False)
    print(f"Guardado: {ruta}")


def obtener_cliente_proyecto(blob_name):
    partes = blob_name.split("/")
    cliente = partes[0]
    proyecto = partes[1] if len(partes) > 1 else "General"
    return cliente, proyecto


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cliente")
    parser.add_argument("--proyecto")
    return parser.parse_args()


def main():
    args = parse_args()

    for blob in bucket.list_blobs():
        if not blob.name.lower().endswith(".pdf"):
            continue

        cliente, proyecto = obtener_cliente_proyecto(blob.name)

        if args.cliente and cliente != args.cliente:
            continue
        if args.proyecto and proyecto != args.proyecto:
            continue

        print(f"Procesando {blob.name}...")
        filas = procesar_factura(blob)
        guardar_excel(cliente, proyecto, filas)


if __name__ == "__main__":
    main()

