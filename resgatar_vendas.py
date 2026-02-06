import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv # Importe isso (já deve estar no seu venv)

# 1. Carrega o arquivo .env local
load_dotenv() 

def inicializar_firebase():
    if not firebase_admin._apps:
        # Tenta pegar da variável de ambiente (como no Render)
        env_config = os.getenv('FIREBASE_CONFIG')
        
        if env_config:
            # Se achou a variável, transforma a string JSON em um dicionário Python
            creds_dict = json.loads(env_config)
            
            # Limpeza da chave privada (correção de quebras de linha)
            if 'private_key' in creds_dict:
                creds_dict['private_key'] = creds_dict['private_key'].replace('\\n', '\n')
            
            cred = credentials.Certificate(creds_dict)
            firebase_admin.initialize_app(cred)
            print(">>> [SUCESSO] Firebase inicializado via Variável de Ambiente!")
        else:
            # Se não achou a variável, dá um erro explicativo
            print(">>> [ERRO] Variável FIREBASE_CONFIG não encontrada localmente.")
            print(">>> Verifique se você criou o arquivo .env corretamente.")
            exit()

inicializar_firebase()
db = firestore.client()

def injetar_venda(payment_id, nome_cliente, email, valor, data_iso, escola, criança, produtos_lista):
    """
    Injeta a venda no Firestore com a estrutura exata que o Dashboard e o Portal esperam.
    data_iso deve estar no formato: '2026-01-31T15:30:00'
    """
    venda_ref = db.collection('vendas').document()
    
    dados_venda = {
        "payment_id": payment_id,
        "merchant_order_id": f"RECUPERADO_{payment_id}",
        "cliente_nome": nome_cliente,
        "cliente_email": email,
        "valor": float(valor),
        "data_hora": datetime.datetime.fromisoformat(data_iso),
        "cliente_escola": escola, # <--- O NOME DEVE SER O DA LISTA ACIMA
        "nome_crianca": criança,
        "status_cielo_codigo": 2, # Forçamos status de Pago
        "status_cielo_mensagem": "Operação Capturada",
        "tipo_pagamento": "Cartão (Resgatado)",
        "produtos": produtos_lista, # Ex: [{"name": "Kit Escolar", "price": valor}]
        "recuperada": True
    }
    
    venda_ref.set(dados_venda)
    print(f"✅ Venda de {nome_cliente} (Escola: {escola}) injetada com sucesso!")

# --- ÁREA DE LANÇAMENTO MANUAL ---
# Agora, para cada venda que você achou na Cielo e não está no sistema, 
# você adiciona uma linha abaixo e roda o script:

# EXEMPLO DE LANÇAMENTO:
# injetar_venda(
#    payment_id="123456789", 
#    nome_cliente="Maria Souza", 
#    email="maria@email.com", 
#    valor=450.00, 
#    data_iso="2026-01-30T10:00:00", 
#    escola="Mundo Encantado", 
#    criança="Pedro Souza",
#    produtos_lista=[{"name": "KIT Amarelinha", "price": 450.00}]
# )

# ADICIONE SUAS VENDAS AQUI ABAIXO:
# ---------------------------------------------------------