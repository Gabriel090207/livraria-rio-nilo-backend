# nfe.py
# ------------------------------------------------------------
# MÓDULO DE GERAÇÃO DE NF-e (XML – SEM ASSINATURA / SEM SEFAZ)
# Compatível com backend Flask + Firestore
# ------------------------------------------------------------

import re
import os
import base64
import tempfile
import datetime
from datetime import timezone
from lxml import etree


# ------------------------------------------------------------
# UTILITÁRIOS
# ------------------------------------------------------------
def somente_numeros(valor: str) -> str:
    if not valor:
        return ""
    return re.sub(r"\D", "", str(valor))


def obter_caminho_certificado():
    """
    Reconstrói o certificado .pfx a partir da variável
    de ambiente CERT_PFX_BASE64 e retorna o caminho temporário.
    (Ainda não usado na assinatura neste passo.)
    """
    cert_base64 = os.getenv("CERT_PFX_BASE64")
    cert_password = os.getenv("CERT_PFX_PASSWORD")

    if not cert_base64 or not cert_password:
        raise RuntimeError("Certificado digital não configurado no ambiente (CERT_PFX_BASE64 / CERT_PFX_PASSWORD).")

    cert_bytes = base64.b64decode(cert_base64)

    temp_dir = tempfile.gettempdir()
    cert_path = os.path.join(temp_dir, "certificado_nfe.pfx")

    with open(cert_path, "wb") as f:
        f.write(cert_bytes)

    return cert_path, cert_password


def calcular_dv_chave_nfe(chave):
    pesos = [2,3,4,5,6,7,8,9] * 6
    soma = 0

    for i, num in enumerate(reversed(chave)):
        soma += int(num) * pesos[i]

    resto = soma % 11
    return 0 if resto in [0,1] else 11 - resto


def gerar_chave_nfe(cUF, cnpj, modelo, serie, numero, tpEmis="1"):
    agora = datetime.datetime.now()
    AAMM = agora.strftime("%y%m")

    cUF = str(cUF).zfill(2)
    cnpj = somente_numeros(cnpj).zfill(14)
    modelo = str(modelo).zfill(2)
    serie = str(serie).zfill(3)
    numero = str(numero).zfill(9)
    tpEmis = str(tpEmis)

    cNF = f"{datetime.datetime.now().microsecond:08d}"

    chave_sem_dv = (
        cUF + AAMM + cnpj + modelo + serie +
        numero + tpEmis + cNF
    )

    dv = calcular_dv_chave_nfe(chave_sem_dv)

    return chave_sem_dv + str(dv)



# ------------------------------------------------------------
# GERADOR DE XML DA NF-e (rascunho técnico)
# ------------------------------------------------------------
def gerar_xml_nfe(venda: dict, itens: list, ambiente: str = "2", serie: str = "2", numero_nfe: str = "1"):
    """
    ambiente:
        "1" = Produção
        "2" = Homologação

    serie:
        use "2" para evitar conflito com o sistema manual (recomendado)

    numero_nfe:
        neste passo ainda é fixo (vamos automatizar com Firestore no próximo passo)
    """

    NS = "http://www.portalfiscal.inf.br/nfe"

    root = etree.Element("NFe", xmlns=NS)

    # ⚠️ Id real da NF-e depende da CHAVE (44 dígitos). Vamos montar corretamente depois.
    # Id provisório (único por emissão)
    chave_nfe = gerar_chave_nfe(
        cUF="29",
        cnpj="19291176000178",
        modelo="55",
        serie=serie,
        numero=numero_nfe
    )

    id_nfe = "NFe" + chave_nfe


    infNFe = etree.SubElement(
        root,
        "infNFe",
        Id=id_nfe,
        versao="4.00"
    )


    # ------------------------------------------------------------
    # IDE
    # ------------------------------------------------------------
    ide = etree.SubElement(infNFe, "ide")
    etree.SubElement(ide, "cUF").text = "29"  # BA
    etree.SubElement(ide, "natOp").text = "Venda de mercadoria"
    etree.SubElement(ide, "mod").text = "55"
    etree.SubElement(ide, "serie").text = str(serie)
    etree.SubElement(ide, "nNF").text = str(numero_nfe)
    etree.SubElement(ide, "tpNF").text = "1"

    # data/hora em UTC
    etree.SubElement(ide, "dhEmi").text = datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S-00:00")

    etree.SubElement(ide, "tpAmb").text = ambiente
    etree.SubElement(ide, "finNFe").text = "1"
    etree.SubElement(ide, "indFinal").text = "1"
    etree.SubElement(ide, "indPres").text = "2"  # Internet
    etree.SubElement(ide, "procEmi").text = "0"
    etree.SubElement(ide, "verProc").text = "1.0"

    # ------------------------------------------------------------
    # EMITENTE (DADOS REAIS)
    # ------------------------------------------------------------
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
    etree.SubElement(emit, "CRT").text = "3"  # Lucro Presumido (CRT 3)

    # ------------------------------------------------------------
    # DESTINATÁRIO (CPF)
    # ------------------------------------------------------------
    dest = etree.SubElement(infNFe, "dest")
    cpf_limpo = somente_numeros(venda.get("cliente_cpf"))
    etree.SubElement(dest, "CPF").text = cpf_limpo
    etree.SubElement(dest, "xNome").text = venda.get("cliente_nome", "Consumidor Final")

    # ------------------------------------------------------------
    # PRODUTOS
    # ------------------------------------------------------------
    total_nf = 0.0

    for i, item in enumerate(itens, start=1):
        det = etree.SubElement(infNFe, "det", nItem=str(i))
        prod = etree.SubElement(det, "prod")

        nome = item.get("name", "Produto")

        preco_raw = str(item.get("price", "0"))
        preco_limpo = (
            preco_raw.replace("R$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )
        try:
            preco = float(preco_limpo)
        except Exception:
            preco = 0.0

        etree.SubElement(prod, "cProd").text = str(i)
        etree.SubElement(prod, "xProd").text = nome
        etree.SubElement(prod, "NCM").text = "49019900"
        etree.SubElement(prod, "CFOP").text = "5102"
        etree.SubElement(prod, "uCom").text = "UN"
        etree.SubElement(prod, "qCom").text = "1"
        etree.SubElement(prod, "vUnCom").text = f"{preco:.2f}"
        etree.SubElement(prod, "vProd").text = f"{preco:.2f}"

        # -------------------------------
        # IMPOSTOS — ICMS ISENTO (LIVRO)
        # -------------------------------
        imposto = etree.SubElement(det, "imposto")
        icms = etree.SubElement(imposto, "ICMS")
        icms40 = etree.SubElement(icms, "ICMS40")

        etree.SubElement(icms40, "orig").text = "0"
        etree.SubElement(icms40, "CST").text = "40"   # Isento
        etree.SubElement(icms40, "vICMS").text = "0.00"


        total_nf += preco

    # ------------------------------------------------------------
    # TOTAL (rascunho)
    # ------------------------------------------------------------
    total = etree.SubElement(infNFe, "total")
    icmsTot = etree.SubElement(total, "ICMSTot")
    etree.SubElement(icmsTot, "vProd").text = f"{total_nf:.2f}"
    etree.SubElement(icmsTot, "vNF").text = f"{total_nf:.2f}"

    # ------------------------------------------------------------
    # PAGAMENTO — PIX (modo padrão por enquanto)
    # ------------------------------------------------------------
    pag = etree.SubElement(infNFe, "pag")
    detPag = etree.SubElement(pag, "detPag")

    # 17 = PIX
    etree.SubElement(detPag, "tPag").text = "17"
    etree.SubElement(detPag, "vPag").text = f"{total_nf:.2f}"


    return etree.tostring(root, pretty_print=True, encoding="UTF-8", xml_declaration=True).decode("utf-8")



from signxml import XMLSigner, methods
from cryptography.hazmat.primitives.serialization import pkcs12


def assinar_xml_nfe(xml_string: str):
    """
    Assina o XML da NF-e conforme padrão SEFAZ (XMLDSig enveloped)
    """
    cert_path, cert_password = obter_caminho_certificado()

    with open(cert_path, "rb") as f:
        pfx_data = f.read()

    private_key, certificate, _ = pkcs12.load_key_and_certificates(
        pfx_data,
        cert_password.encode()
    )

    xml_root = etree.fromstring(xml_string.encode("utf-8"))

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha1",
        digest_algorithm="sha1",
        c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
    )

    signed_xml = signer.sign(
        xml_root,
        key=private_key,
        cert=certificate,
        reference_uri="#" + xml_root.find(".//{http://www.portalfiscal.inf.br/nfe}infNFe").get("Id")
    )

    return etree.tostring(
        signed_xml,
        encoding="UTF-8",
        xml_declaration=True
    ).decode("utf-8")


def enviar_nfe_sefaz(xml, ambiente="2"):
    """
    AINDA EM MODO TESTE — NÃO ENVIA PARA SEFAZ
    """
    return {
        "status": "teste",
        "mensagem": "NF-e simulada (SEFAZ ainda não chamado)",
        "xml": xml
    }
