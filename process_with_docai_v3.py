import os
import re
import argparse
import io
import pandas as pd
from PyPDF2 import PdfReader, PdfWriter
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
storage_client = storage.Client()
processor_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"
bucket = storage_client.bucket(BUCKET_NAME)

# ---------------- UTILIDADES ----------------

def parse_amount(valor: str) -> float:
    if not valor: return 0.0
    s = re.sub(r"[^\d,.\-]", "", str(valor))
    if not s: return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s: s = s.replace(",", ".")
    try: return round(float(s), 2)
    except ValueError: return 0.0

def es_justificante_o_basura(texto, data):
    """Detecta justificantes bancarios o páginas de anexos sin datos reales."""
    patrones_pago = [
        r"detalle de orden", r"remesa", r"cuenta ordenante", r"cuenta beneficiario",
        r"justificante de pago", r"transferencia realizada", r"ejecutada", r"abono"
    ]
    texto_min = texto.lower()
    
    # Caso 1: Palabras clave bancarias
    es_banco = any(re.search(p, texto_min) for p in patrones_pago)
    
    # Caso 2: No hay ni proveedor ni número de factura (página vacía o anexo)
    falta_info_critica = not data["Proveedor"] and not data["Nº Factura"]
    
    return es_banco or falta_info_critica

def mover_a_procesados(blob_name):
    try:
        source_blob = bucket.blob(blob_name)
        partes = blob_name.split('/')
        nueva_ruta = f"{partes[0]}/{partes[1]}/procesados/{partes[-1]}" if len(partes) >= 2 else f"procesados/{blob_name}"
        bucket.copy_blob(source_blob, bucket, nueva_ruta)
        source_blob.delete()
    except Exception as e: print(f"  [!] Error al mover: {e}")

# ---------------- EXTRACCIÓN ----------------

def llamar_a_document_ai(content, nombre_referencia):
    raw_document = documentai.RawDocument(content=content, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=processor_name, raw_document=raw_document)
    result = docai_client.process_document(request=request)
    doc = result.document

    data = {
        "Archivo": nombre_referencia, "Proveedor": "", "CIF_Proveedor": "",
        "Cliente": "", "CIF_Cliente": "", "Fecha": "", "Nº Factura": "",
        "Base Imponible": 0.0, "IVA": 0.0, "Total": 0.0, "Concepto": "", "Validación": ""
    }

    base_candidates, iva_candidates, total_candidates, descripciones = [], [], [], []

    for e in doc.entities:
        t, text = e.type_, e.mention_text or ""
        if t == "supplier_name": data["Proveedor"] = text.replace("\n", " ")
        elif t == "supplier_tax_id": data["CIF_Proveedor"] = text
        elif t == "customer_name": data["Cliente"] = text.replace("\n", " ")
        elif t == "customer_tax_id": data["CIF_Cliente"] = text
        elif t == "invoice_date": data["Fecha"] = text
        elif t == "invoice_id": data["Nº Factura"] = text
        elif t == "total_amount": total_candidates.append(text)
        elif t == "net_amount": base_candidates.append(text)
        elif t == "total_tax_amount": iva_candidates.append(text)
        elif t == "line_item":
            for prop in e.properties:
                if prop.type_ == "line_item/description": descripciones.append(prop.mention_text)

    # Procesar números
    data["Base Imponible"] = max([parse_amount(v) for v in base_candidates] or [0.0])
    data["IVA"] = max([parse_amount(v) for v in iva_candidates] or [0.0])
    data["Total"] = max([parse_amount(v) for v in total_candidates] or [0.0])
    data["Concepto"] = " | ".join(descripciones).strip().replace("\n", " ")

    # FILTRO DE SEGURIDAD: Justificantes y Páginas vacías
    if es_justificante_o_basura(doc.text, data):
        print(f"  [-] Saltando: {nombre_referencia} (No parece una factura)")
        return None

    # VALIDACIÓN CONTABLE
    suma_calculada = round(data["Base Imponible"] + data["IVA"], 2)
    if data["Total"] > 0 and abs(suma_calculada - data["Total"]) < 0.05:
        data["Validación"] = "CORRECTA"
    else:
        data["Validación"] = "REVISAR"

    return data

# ---------------- PROCESAMIENTO ----------------

def procesar_archivo(blob):
    content = blob.download_as_bytes()
    pdf_reader = PdfReader(io.BytesIO(content))
    total_pags = len(pdf_reader.pages)
    resultados = []
    
    for i in range(total_pags):
        writer = PdfWriter()
        writer.add_page(pdf_reader.pages[i])
        with io.BytesIO() as buf:
            writer.write(buf)
            ref = f"{blob.name} (pág {i+1})" if total_pags > 1 else blob.name
            print(f"    Analizando pág {i+1}/{total_pags}...")
            res = llamar_a_document_ai(buf.getvalue(), ref)
            if res: resultados.append(res)
    return resultados

# ---------------- MAIN ----------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cliente")
    parser.add_argument("--proyecto")
    args = parser.parse_args()

    acumulador = {}
    blobs = [b for b in bucket.list_blobs() if b.name.lower().endswith(".pdf") and "/procesados/" not in b.name]
    
    for blob in blobs:
        partes = blob.name.split("/")
        cli, proy = partes[0], (partes[1] if len(partes) > 1 else "General")
        if args.cliente and cli != args.cliente: continue
        if args.proyecto and proy != args.proyecto: continue

        print(f"\n--- Procesando: {blob.name} ---")
        filas = procesar_archivo(blob)
        if filas:
            key = (cli, proy)
            if key not in acumulador: acumulador[key] = []
            acumulador[key].extend(filas)
        mover_a_procesados(blob.name)

    for (cli, proy), filas in acumulador.items():
        ruta = os.path.join(OUTPUT_DIR, f"{cli}_{proy}.xlsx")
        df_nuevo = pd.DataFrame(filas)
        if os.path.exists(ruta):
            df_nuevo = pd.concat([pd.read_excel(ruta), df_nuevo], ignore_index=True)
        df_nuevo.to_excel(ruta, index=False)
        print(f"\n[ÉXITO] Excel actualizado: {ruta}")

if __name__ == "__main__":
    main()