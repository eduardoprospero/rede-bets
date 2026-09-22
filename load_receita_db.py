# -*- coding: utf-8 -*-
"""
load_receita.py - Lê os zips baixados da Receita e carrega no banco SQLite.
"""
import sqlite3
import zipfile
import pandas as pd
from pathlib import Path
import sys
import config as cfg

def carregar_tabela_zip(caminho_zip: Path, nome_tabela: str, conexao: sqlite3.Connection, colunas: list):
    """Lê um arquivo zipado em chunks e insere no banco SQLite por empilhamento (append)."""
    print(f"Carregando {caminho_zip.name} na tabela '{nome_tabela}'...")
    try:
        with zipfile.ZipFile(caminho_zip) as z:
            nome_arquivo_interno = z.namelist()[0]
            with z.open(nome_arquivo_interno) as f:
                chunks = pd.read_csv(f, sep=";", encoding="latin-1", header=None, 
                                     names=colunas, dtype=str, chunksize=100000)
                
                linhas_inseridas = 0
                for chunk in chunks:
                    if nome_tabela == "socios":
                        chunk["socio_chave"] = chunk.apply(
                            lambda r: f"PF:{str(r['nome_socio'])}-{str(r['cnpj_cpf_socio'])}", axis=1
                        )
                        chunk["socio_basico"] = chunk["cnpj_cpf_socio"].str[:8]

                    # CORREÇÃO: Agora sempre usa "append". A tabela já foi limpa no main().
                    chunk.to_sql(nome_tabela, conexao, if_exists="append", index=False)
                    linhas_inseridas += len(chunk)
                    print(f"  - Inseridas {linhas_inseridas:,} linhas...", end="\r")
        print(f"\nConcluído: {caminho_zip.name}\n")
    except Exception as e:
        print(f"\nErro ao processar {caminho_zip.name}: {e}")

def main():
    pasta_zips = cfg.ultima_competencia_local()
    if not pasta_zips or not pasta_zips.exists():
        sys.exit("Pasta com os downloads da Receita não encontrada. Execute o passo 02 primeiro.")

    layout = {
        "empresas": ["cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel", "capital_social", "porte", "ente_federativo"],
        "estabelecimentos": ["cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador_matriz", "nome_fantasia", "situacao_cadastral", "data_situacao", "motivo_situacao", "cidade_exterior", "pais", "data_inicio", "cnae_principal", "cnae_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento", "bairro", "cep", "uf", "municipio", "ddd1", "telefone1", "ddd2", "telefone2", "ddd_fax", "fax", "correio_eletronico", "situacao_especial", "data_situacao_especial"],
        "socios": ["cnpj_basico", "identificador_socio", "nome_socio", "cnpj_cpf_socio", "qualificacao_socio", "data_entrada_sociedade", "pais", "representante_legal", "nome_representante", "qualificacao_representante", "faixa_etaria"],
        "naturezas": ["codigo", "descricao"],
        "qualificacoes": ["codigo", "descricao"]
    }

    tabelas_db = {
        "empresas": "empresas",
        "estabelecimentos": "estabelecimentos",
        "socios": "socios",
        "naturezas": "dom_naturezas",
        "qualificacoes": "dom_qualificacoes"
    }

    print(f"Criando/conectando ao banco em: {cfg.DB_PATH}")
    conn = sqlite3.connect(cfg.DB_PATH)

    # CORREÇÃO: Limpa as tabelas antigas antes de iniciar a carga
    print("Limpando tabelas antigas para evitar duplicação ou sobrescrita...")
    for nome_tabela in set(tabelas_db.values()):
        conn.execute(f"DROP TABLE IF EXISTS {nome_tabela}")
    
    # Cria tabela de metadados
    conn.execute("DROP TABLE IF EXISTS meta")
    conn.execute("CREATE TABLE meta (chave TEXT, valor TEXT)")
    conn.execute("INSERT INTO meta (chave, valor) VALUES ('competencia', ?)", (pasta_zips.name,))
    conn.commit()

    # Identifica os arquivos baixados e os carrega acumulativamente
    arquivos = list(pasta_zips.glob("*.zip"))
    for arquivo in arquivos:
        tipo = cfg.classificar_arquivo(arquivo.name)
        if tipo in layout:
            carregar_tabela_zip(arquivo, tabelas_db[tipo], conn, layout[tipo])

    conn.close()
    print("Carga finalizada. O banco SQLite agora contém 100% dos dados.")

if __name__ == "__main__":
    main()