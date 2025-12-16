# nfe.py
# ------------------------------------------------------------
# MÓDULO DE GERAÇÃO DE NF-e (RAScunho técnico – SEM SEFAZ)
# Compatível com backend Flask + Firestore
# ------------------------------------------------------------

import datetime
from lxml import etree


def gerar_xml_nfe(venda, itens, ambiente="2"):
    """
    Gera um XML de NF-e em formato técnico (ainda NÃO fiscalmente válido).
    Este XML serve para:
    - validar integração
    - testar fluxo
    - preparar estrutura real da NF-e

    ambiente:
        "1" = Produção
        "2" = Homologação
    """

    NS = "http://www.portalfiscal.inf.br/nfe"

    # ------------------------------------------------------------
    # ROOT
    # ------------------------------------------------------------
    root = etree.Element("NFe", xmlns=NS)

    infNFe = etree.SubElement(
        root,
        "infNFe",
        Id="NFeTESTE00000000000000000000000000000000000000000",
        versao="4.00"
    )

    # ------------------------------------------------------------
    # IDE
    # ------------------------------------------------------------
    ide = etree.SubElement(infNFe, "ide")
    etree.SubElement(ide, "cUF").text = "29"            # Bahia
    etree.SubElement(ide, "natOp").text = "Venda de mercadoria"
    etree.SubElement(ide, "mod").text = "55"
    etree.SubElement(ide, "serie").text = "1"
    etree.SubElement(ide, "nNF").text = "1"
    etree.SubElement(ide, "tpNF").text = "1"
    etree.SubElement(ide, "dhEmi").text = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S-00:00")
    etree.SubElement(ide, "tpAmb").text = ambiente
    etree.SubElement(ide, "finNFe").text = "1"
    etree.SubElement(ide, "indFinal").text = "1"
    etree.SubElement(ide, "indPres").text = "2"         # Internet
    etree.SubElement(ide, "procEmi").text = "0"
    etree.SubElement(ide, "verProc").text = "1.0"

    # ------------------------------------------------------------
    # EMITENTE (DADOS MOCK – depois serão reais)
    # ------------------------------------------------------------
    emit = etree.SubElement(infNFe, "emit")
    etree.SubElement(emit, "CNPJ").text = "12345678000195"
    etree.SubElement(emit, "xNome").text = "Livraria Rio Nilo"
    etree.SubElement(emit, "xFant").text = "Livraria Rio Nilo"

    enderEmit = etree.SubElement(emit, "enderEmit")
    etree.SubElement(enderEmit, "xLgr").text = "Rua Exemplo"
    etree.SubElement(enderEmit, "nro").text = "123"
    etree.SubElement(enderEmit, "xBairro").text = "Centro"
    etree.SubElement(enderEmit, "cMun").text = "2927408"
    etree.SubElement(enderEmit, "xMun").text = "Salvador"
    etree.SubElement(enderEmit, "UF").text = "BA"
    etree.SubElement(enderEmit, "CEP").text = "40000000"
    etree.SubElement(enderEmit, "cPais").text = "1058"
    etree.SubElement(enderEmit, "xPais").text = "BRASIL"

    etree.SubElement(emit, "IE").text = "123456789"
    etree.SubElement(emit, "CRT").text = "3"  # Lucro Presumido

    # ------------------------------------------------------------
    # DESTINATÁRIO (CPF)
    # ------------------------------------------------------------
    dest = etree.SubElement(infNFe, "dest")
    etree.SubElement(dest, "CPF").text = venda.get("cliente_cpf", "00000000000")
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
        except:
            preco = 0.0

        etree.SubElement(prod, "cProd").text = str(i)
        etree.SubElement(prod, "xProd").text = nome
        etree.SubElement(prod, "NCM").text = "49019900"
        etree.SubElement(prod, "CFOP").text = "5102"
        etree.SubElement(prod, "uCom").text = "UN"
        etree.SubElement(prod, "qCom").text = "1"
        etree.SubElement(prod, "vUnCom").text = f"{preco:.2f}"
        etree.SubElement(prod, "vProd").text = f"{preco:.2f}"

        total_nf += preco

    # ------------------------------------------------------------
    # TOTAL
    # ------------------------------------------------------------
    total = etree.SubElement(infNFe, "total")
    icmsTot = etree.SubElement(total, "ICMSTot")

    etree.SubElement(icmsTot, "vProd").text = f"{total_nf:.2f}"
    etree.SubElement(icmsTot, "vNF").text = f"{total_nf:.2f}"

    # ------------------------------------------------------------
    # RETORNO FINAL
    # ------------------------------------------------------------
    return etree.tostring(
        root,
        pretty_print=True,
        encoding="UTF-8",
        xml_declaration=True
    ).decode("utf-8")


def enviar_nfe_sefaz(xml_assinado, ambiente="2"):
    """
    MODO TESTE — NÃO ENVIA PARA SEFAZ
    """
    return {
        "status": "teste",
        "mensagem": "NF-e simulada (SEFAZ ainda não chamado)",
        "xml": xml_assinado
    }

if __name__ == "__main__":
    xml = gerar_xml_nfe(
        venda={
            "cliente_nome": "Cliente Teste",
            "cliente_cpf": "12345678909"
        },
        itens=[
            {"name": "Livro Matemática", "price": "R$ 120,00"},
            {"name": "Livro Português", "price": "R$ 80,00"}
        ]
    )
    print(xml)
