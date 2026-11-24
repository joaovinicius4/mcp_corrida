# Coach de Corrida

Histórico local de corrida e bike com importação de arquivos, fontes por campo,
cálculos reproduzíveis e acesso por MCP. A regra principal do coach é ser honesto:
separar dados observados, hipóteses e informações desconhecidas.

## Estado da primeira entrega

- Importação da aba `Corridas` de XLSX, de FIT com uma sessão e das evidências
  já transcritas e confirmadas na conversa.
- Importação dos CSVs do Zepp como registros brutos separados. FC diária não é
  transformada em FC de corrida.
- Banco SQLite, cópias verificadas dos originais, identificação por SHA-256,
  enriquecimento de atividades e relatórios de qualidade.
- Histórico com paginação, detalhes, voltas, amostras FIT e análise por trecho.
- Totais por período/modalidade, revisão semanal, candidatos a comparação e
  comparação de duas atividades com fontes e limitações explícitas.
- Relatos e planos com rascunho, confirmação e versões preservadas.
- MCP por `stdio` e Streamable HTTP local. Somente leitura por padrão.
- Backup do banco junto com os originais e exportação JSON.

Não há conexão com contas externas, serviço em nuvem, aplicativo Android,
sincronização automática ou cliente ChatGPT configurado. O portfólio JV.RUNNER
e suas credenciais não participam da execução.

## Instalação no Windows

Python 3.11 ou mais recente. Nesta máquina, `.venv` já foi criada e o pacote já
está instalado. Para uma instalação nova, execute na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

O arquivo `requirements-lock.txt` registra as versões resolvidas no ambiente de
validação. Para reproduzi-las em ambiente compatível:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

Não é necessário ativar o ambiente virtual nem alterar a política de execução
do PowerShell. Use os executáveis da pasta `.venv\Scripts`.

## Consultar os dados já importados

```powershell
.\.venv\Scripts\coach.exe quality
.\.venv\Scripts\coach.exe activities --start 2026-11-01 --end 2026-11-30
.\.venv\Scripts\coach.exe activities --start 2026-06-28 --end 2026-06-28
.\.venv\Scripts\coach.exe period 2026-01-01 2026-11-23
.\.venv\Scripts\coach.exe week 2026-11-16
.\.venv\Scripts\coach.exe context
```

Datas são inclusivas. Sem registro em um período, o sistema informa a ausência
de dados; não presume que você ficou parado. A planilha fornecida contém apenas
corridas. O comando de período apresenta modalidades em grupos separados.

Use os IDs retornados por `activities` nos comandos seguintes:

```powershell
.\.venv\Scripts\coach.exe detail 1
.\.venv\Scripts\coach.exe candidates 1
.\.venv\Scripts\coach.exe compare 1 2
```

Os IDs acima são exemplos. A comparação contém diferença de ritmo/FC e nunca
converte isso em uma porcentagem de condicionamento perdido.

## Adicionar um treino

Coloque o FIT no projeto ou indique seu caminho:

```powershell
.\.venv\Scripts\coach.exe import "C:\caminho\treino.fit"
```

A importação é explícita: adicionar um arquivo à pasta não inicia uma rotina
oculta. O comando `bootstrap` procura os arquivos já conhecidos do projeto e
os FITs da pasta principal; pode ser repetido sem duplicar as mesmas fontes:

```powershell
.\.venv\Scripts\coach.exe bootstrap
```

Para unir uma sessão FIT quando o vínculo automático é ambíguo, confira o ID e
use `import arquivo.fit --activity-id ID`. O destino deve ter a mesma data e
modalidade. O relatório mostra atividades criadas e enriquecidas.

FIT tem prioridade sobre os valores arredondados da planilha. Prints e relatos
mantêm origem própria; um valor de FC escrito em uma descrição não preenche
automaticamente a FC medida. Arquivos FIT multiesporte são rejeitados por ora,
para evitar atribuir amostras à modalidade errada.

Para FIT com dados detalhados:

```powershell
.\.venv\Scripts\coach.exe samples ID --limit 200 --offset 0
.\.venv\Scripts\coach.exe segment ID 2 4
```

O trecho usa as amostras entre 2 e 4 km, mostra limites realmente registrados e
lacunas. A média de FC das amostras é aritmética e não é apresentada como uma
média ponderada pelo tempo ou como a FC média oficial da sessão.

## Relatos e planos

```powershell
.\.venv\Scripts\coach.exe note 2026-11-24 "Treino confortável; sem dor" --rpe 3
.\.venv\Scripts\coach.exe confirm ID_DO_RASCUNHO APROVAR
```

O primeiro comando cria apenas um rascunho, que não aparece como relato
confirmado. Leia o conteúdo antes de executar o segundo. Repetir a confirmação
não duplica o relato.

O arquivo `examples/semana_exemplo.json` reproduz a primeira semana discutida
como exemplo técnico. Ele **não foi salvo como plano aprovado**:

```powershell
.\.venv\Scripts\coach.exe plan examples/semana_exemplo.json
.\.venv\Scripts\coach.exe confirm ID_DO_RASCUNHO APROVAR
```

Cada plano aprovado da mesma semana cria uma nova versão. O código valida a
estrutura, datas e durações; não certifica a adequação esportiva do plano.

## Usar o MCP

```powershell
.\.venv\Scripts\coach-mcp.exe --data-dir "C:\caminho\mcp_corrida\data"
```

Esse comando espera um cliente MCP em `stdio`, portanto não abre uma interface
visual e não deve imprimir mensagens comuns no stdout. Os modelos de
configuração estão em `examples/mcp-codex.toml` e `examples/mcp-vscode.json`.
Cada cliente lê sua própria configuração. Copiar o modelo não conecta o servidor
automaticamente a este chat, e o código não altera configurações globais.

Ferramentas de leitura:

- `consultar_contexto`, `listar_atividades`, `detalhar_atividade`;
- `consultar_amostras`, `analisar_trecho`, `buscar_comparaveis`;
- `comparar_atividades`, `resumir_periodo`, `revisar_semana`;
- `verificar_qualidade`.

Para permitir rascunhos e confirmação, acrescente `--enable-writes` ao comando
do cliente. Isso habilita `propor_relato`, `propor_plano` e
`confirmar_rascunho`. Configure o cliente para pedir aprovação humana de escrita.
O protocolo de duas etapas e a frase `APROVAR` impedem confirmação acidental,
mas o servidor sozinho não prova que uma pessoa clicou em aprovar: essa etapa
pertence à interface cliente e às suas permissões.

Prompts úteis:

> Consulte meu contexto e revise a semana de 16/11/2026. Separe fatos de hipóteses.

> Encontre treinos comparáveis à corrida de 20/11 e explique as limitações.

> Mostre as fontes dos batimentos da meia de junho e do FIT de outubro.

Para testes HTTP locais, use `coach-mcp.exe --http-port 8765`. O endereço é
`http://127.0.0.1:8765/mcp`; o servidor não aceita um endereço público por esta
interface. Hospedar para ChatGPT exige validar disponibilidade na conta,
autenticação, HTTPS/túnel e permissões antes de expor dados pessoais.

## Backup e exportação

```powershell
.\.venv\Scripts\coach.exe backup outputs/backup-local
.\.venv\Scripts\coach.exe export outputs/historico.json
.\.venv\Scripts\coach.exe export outputs/historico-completo.json --include-raw
```

Os destinos precisam ser novos. Backup inclui o banco e os originais. Exportação
inclui proveniência; `--include-raw` acrescenta os registros CSV e as amostras
FIT. Copiar o backup para outra máquina preserva o histórico: execute com
`--data-dir caminho/do/backup`. O arquivo de banco isolado não inclui os originais.

`data/`, exportações, fontes pessoais e a cópia do portfólio estão no `.gitignore`.
A pasta local continua contendo dados pessoais: o `.gitignore` não é criptografia
nem cópia externa de segurança.

## Verificação

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

Os testes cobrem importação, origem dos valores, ausência versus zero,
idempotência, horários, arquivos corrompidos, FC diária separada, parciais,
comparações, bike, backup, CLI e o protocolo MCP real. Testes com arquivos pessoais
são pulados quando esses originais não estão presentes numa nova instalação.

Veja `docs/arquitetura.md` para os critérios e limites da implementação.
