# nfe.py
# ------------------------------------------------------------
# NF-e COMPLETA: Geração + Assinatura + Envio SEFAZ (SOAP)
# ------------------------------------------------------------

import os
import re
import time
import base64
import tempfile
import datetime
import requests
from datetime import timezone
from lxml import etree

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization
from signxml import XMLSigner, methods


# ============================================================
# UTILITÁRIOS
# ============================================================

def somente_numeros(valor: str) -> str:
    if not valor:
        return ""
    return re.sub(r"\D", "", str(valor))


def obter_caminho_certificado():
    cert_base64 = os.getenv("CERT_PFX_BASE64")
    cert_password = os.getenv("CERT_PFX_PASSWORD")

    if not cert_base64 or not cert_password:
        raise RuntimeError("CERT_PFX_BASE64 ou CERT_PFX_PASSWORD não configurado")

    cert_bytes = base64.b64decode(cert_base64)
    temp_dir = tempfile.gettempdir()
    cert_path = os.path.join(temp_dir, "certificado_nfe.pfx")

    with open(cert_path, "wb") as f:
        f.write(cert_bytes)

    return cert_path, cert_password


def obter_cert_pem_paths():
    cert_path, cert_password = obter_caminho_certificado()

    with open(cert_path, "rb") as f:
        pfx_data = f.read()

    private_key, certificate, _ = pkcs12.load_key_and_certificates(
        pfx_data, cert_password.encode()
    )

    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()
    )

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)

    temp_dir = tempfile.gettempdir()
    key_file = os.path.join(temp_dir, "nfe_key.pem")
    cert_file = os.path.join(temp_dir, "nfe_cert.pem")

    with open(key_file, "wb") as f:
        f.write(key_pem)

    with open(cert_file, "wb") as f:
        f.write(cert_pem)

    return cert_file, key_file


# ============================================================
# CHAVE NF-e
# ============================================================

def calcular_dv_chave_nfe(chave):
    pesos = [2,3,4,5,6,7,8,9] * 6
    soma = sum(int(n) * pesos[i] for i, n in enumerate(reversed(chave)))
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def gerar_chave_nfe(cUF, cnpj, modelo, serie, numero):
    agora = datetime.datetime.now()
    AAMM = agora.strftime("%y%m")

    cUF = str(cUF).zfill(2)
    cnpj = somente_numeros(cnpj).zfill(14)
    modelo = str(modelo).zfill(2)
    serie = str(serie).zfill(3)
    numero = str(numero).zfill(9)
    tpEmis = "1"
    cNF = f"{agora.microsecond:08d}"

    base = cUF + AAMM + cnpj + modelo + serie + numero + tpEmis + cNF
    dv = calcular_dv_chave_nfe(base)
    return base + str(dv)


# ============================================================
# XML NF-e
# ============================================================

def gerar_xml_nfe(venda, itens, ambiente="2", serie="2", numero_nfe="1"):
    NS = "http://www.portalfiscal.inf.br/nfe"

    chave = gerar_chave_nfe(
        cUF="29",
        cnpj="19291176000178",
        modelo="55",
        serie=serie,
        numero=numero_nfe
    )

    root = etree.Element("NFe", xmlns=NS)
    infNFe = etree.SubElement(root, "infNFe", Id=f"NFe{chave}", versao="4.00")

    # ================= IDE =================
    ide = etree.SubElement(infNFe, "ide")
    etree.SubElement(ide, "cUF").text = "29"
    etree.SubElement(ide, "natOp").text = "Venda de mercadoria"
    etree.SubElement(ide, "mod").text = "55"
    etree.SubElement(ide, "serie").text = serie
    etree.SubElement(ide, "nNF").text = numero_nfe
    etree.SubElement(ide, "tpNF").text = "1"
    etree.SubElement(ide, "dhEmi").text = datetime.datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    etree.SubElement(ide, "tpAmb").text = ambiente
    etree.SubElement(ide, "finNFe").text = "1"
    etree.SubElement(ide, "indFinal").text = "1"
    etree.SubElement(ide, "indPres").text = "2"
    etree.SubElement(ide, "procEmi").text = "0"
    etree.SubElement(ide, "verProc").text = "1.0"

    # ================= EMITENTE =================
    emit = etree.SubElement(infNFe, "emit")
    etree.SubElement(emit, "CNPJ").text = "19291176000178"
    etree.SubElement(emit, "xNome").text = "Livraria e Distribuidora Rio Nilo Ltda"
    etree.SubElement(emit, "xFant").text = "Livraria Rio Nilo"

    enderEmit = etree.SubElement(emit, "enderEmit")
    etree.SubElement(enderEmit, "xLgr").text = "Avenida Aliomar Baleeiro"
    etree.SubElement(enderEmit, "nro").text = "2262"
    etree.SubElement(enderEmit, "xBairro").text = "Jd Cajazeiras"
    etree.SubElement(enderEmit, "cMun").text = "2927408"
    etree.SubElement(enderEmit, "xMun").text = "Salvador"
    etree.SubElement(enderEmit, "UF").text = "BA"
    etree.SubElement(enderEmit, "CEP").text = "41230455"
    etree.SubElement(enderEmit, "cPais").text = "1058"
    etree.SubElement(enderEmit, "xPais").text = "BRASIL"

    etree.SubElement(emit, "IE").text = "113382426"
    etree.SubElement(emit, "CRT").text = "3"  # Lucro Presumido

    # ================= DESTINATÁRIO =================
    dest = etree.SubElement(infNFe, "dest")
    etree.SubElement(dest, "CPF").text = somente_numeros(venda["cliente_cpf"])
    etree.SubElement(dest, "xNome").text = venda["cliente_nome"]

    # ================= PRODUTOS =================
    total_nf = 0.0

    for i, item in enumerate(itens, start=1):
        det = etree.SubElement(infNFe, "det", nItem=str(i))
        prod = etree.SubElement(det, "prod")

        preco = float(
            str(item["price"])
            .replace("R$", "")
            .replace(".", "")
            .replace(",", ".")
        )

        total_nf += preco

        etree.SubElement(prod, "cProd").text = str(i)
        etree.SubElement(prod, "xProd").text = item["name"]
        etree.SubElement(prod, "NCM").text = "49019900"
        etree.SubElement(prod, "CFOP").text = "5102"
        etree.SubElement(prod, "uCom").text = "UN"
        etree.SubElement(prod, "qCom").text = "1"
        etree.SubElement(prod, "vUnCom").text = f"{preco:.2f}"
        etree.SubElement(prod, "vProd").text = f"{preco:.2f}"

        imposto = etree.SubElement(det, "imposto")
        icms = etree.SubElement(imposto, "ICMS")
        icms40 = etree.SubElement(icms, "ICMS40")
        etree.SubElement(icms40, "orig").text = "0"
        etree.SubElement(icms40, "CST").text = "40"
        etree.SubElement(icms40, "vICMS").text = "0.00"

    # ================= TOTAL =================
    total = etree.SubElement(infNFe, "total")
    icmsTot = etree.SubElement(total, "ICMSTot")
    etree.SubElement(icmsTot, "vProd").text = f"{total_nf:.2f}"
    etree.SubElement(icmsTot, "vNF").text = f"{total_nf:.2f}"

    # ================= PAGAMENTO (PIX) =================
    pag = etree.SubElement(infNFe, "pag")
    detPag = etree.SubElement(pag, "detPag")
    etree.SubElement(detPag, "tPag").text = "17"
    etree.SubElement(detPag, "vPag").text = f"{total_nf:.2f}"

    return etree.tostring(
        root,
        pretty_print=True,
        encoding="UTF-8",
        xml_declaration=True
    ).decode("utf-8")

# ============================================================
# ASSINATURA
# ============================================================

def assinar_xml_nfe(xml):
    cert_path, password = obter_caminho_certificado()

    with open(cert_path, "rb") as f:
        pfx = f.read()

    key, cert, _ = pkcs12.load_key_and_certificates(
        pfx,
        password.encode()
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    root = etree.fromstring(xml.encode("utf-8"))

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256"
    )

    signed = signer.sign(
        root,
        key=key,
        cert=cert_pem,
        reference_uri="#" + root.find(".//{*}infNFe").get("Id")
    )

    return etree.tostring(
        signed,
        encoding="UTF-8",
        xml_declaration=True
    ).decode("utf-8")

# ============================================================
# ENVIO SEFAZ (SOAP)
# ============================================================

def enviar_nfe_sefaz(xml_assinado: str, ambiente: str = "2"):
    return {
        "status": "pendente_envio_sefaz",
        "mensagem": "NF-e gerada e assinada corretamente. Envio à SEFAZ será ativado após liberação do ambiente.",
        "xml_assinado": xml_assinado
    }
