# Arquivo Arcano

Aplicação local para pesquisar regras, poderes, atributos, mecânicas e símbolos em PDFs e DOCX licenciados pelo usuário, lidos de uma pasta deste computador ou de uma pasta escolhida do Google Drive.

## Estado atual

A primeira fatia funcional inclui:

- SQLite com FTS5 e resultados ligados ao livro e à página;
- descoberta recursiva em uma pasta local ou em uma única pasta do Google Drive;
- suporte de descoberta somente para PDF e DOCX;
- adaptador paginado para a Google Drive API;
- sincronização recursiva automática a cada 60 segundos;
- deduplicação por SHA-256, mantendo edições diferentes como 1.0 e 1.1 separadas;
- botão para sincronização manual, com relatório de erros por arquivo;
- extração de texto e coordenadas de PDF com PyMuPDF;
- detecção de páginas que precisam de OCR;
- API FastAPI e interface local responsiva;
- exportação portátil em JSONL para uso por outras ferramentas de IA;
- testes do índice, API, escopo do Drive e ingestão de PDF.

A autenticação OAuth, seleção da pasta, sincronização, perguntas locais com Ollama e busca estruturada de fichas de ameaças já estão conectadas. OCR, visualização de página e catálogo de ícones ainda serão conectados nas próximas etapas.

Agora o catálogo de ícones/imagens também está disponível com auto-tag local por IA (Ollama).

## Executar

Requer Python 3.12 ou mais recente.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m rpg_rules_search
```

Abra `http://127.0.0.1:8765`.

No ambiente atual, as dependências Python principais já estão disponíveis e também é possível executar diretamente:

```bash
PYTHONPATH=src python3 -m rpg_rules_search
```

## Raspberry Pi

O instalador do Raspberry Pi configura o Arquivo Arcano e o Ollama como serviços
do systemd. O Ollama inicia automaticamente no boot, reinicia após falhas e aceita
conexões somente em `127.0.0.1:11434`. Um servidor Ollama remoto é usado apenas
quando configurado explicitamente pela interface.

```bash
curl -fsSL https://raw.githubusercontent.com/HiltonWS/rpg-rules-search/main/scripts/deploy.sh | bash
```

O instalador também habilita uma verificação do GitHub a cada 15 minutos. Quando
há um novo commit em `origin/main`, o Pi atualiza as dependências e units, garante
que o Ollama esteja ativo e reinicia a aplicação. Alterações locais ou histórico
divergente bloqueiam a atualização automática para evitar perda de trabalho.

Consulte [DEPLOYMENT.md](DEPLOYMENT.md) para configuração de rede, OAuth por túnel
SSH, atualização manual, estado dos serviços e diagnóstico por logs.

## Testes

```bash
PYTHONPATH=src python3 -m pytest -q
```

## Dependências do sistema planejadas

Para documentos digitalizados e DOCX, instale:

- LibreOffice, necessário para converter DOCX em PDF com paginação estável;
- Tesseract com idiomas português e inglês;
- OCRmyPDF;
- Ollama, já encontrado no ambiente atual, para perguntas locais.

## Google Drive

A integração usa OAuth 2.0 para aplicativo desktop. Para configurar:

1. No Google Cloud Console, crie ou selecione um projeto.
2. Ative a **Google Drive API**.
3. Configure a tela de consentimento OAuth. Em modo de teste, inclua sua conta em "Usuários de teste".
4. Crie um cliente OAuth do tipo **App para computador** e baixe o arquivo JSON.
5. Abra `http://127.0.0.1:8765`, clique em **Configurar biblioteca** e carregue o JSON.
6. Clique em **Conectar ao Google Drive**, autorize acesso somente de leitura e escolha uma pasta.
7. Clique em **Sincronizar agora**. As próximas verificações ocorrerão automaticamente a cada minuto.

O client secret, o token e o identificador da pasta ficam em `~/.local/share/rpg-rules-search/`, fora do repositório. A descoberta começa na pasta selecionada, percorre somente seus descendentes e ignora qualquer arquivo que não seja PDF ou DOCX.

## Pasta local

Em **Configurar biblioteca**, informe o caminho absoluto de uma pasta local. O Arquivo Arcano percorre suas subpastas e sincroniza PDF e DOCX sem exigir OAuth. Alterações são verificadas automaticamente a cada minuto e também podem ser processadas pelo botão **Sincronizar agora**.

## Ingestão

A sincronização compara o `modifiedTime` de cada arquivo do Drive com o catálogo local:

- arquivos novos ou alterados são baixados e reindexados;
- arquivos com conteúdo SHA-256 idêntico são catalogados como duplicatas, mas não têm páginas indexadas novamente;
- arquivos com nomes de versão, como `AS 1.0.pdf` e `AS 1.1.pdf`, permanecem separados quando o conteúdo for diferente;
- arquivos sem alteração não são baixados novamente;
- arquivos removidos ou movidos para fora da pasta selecionada têm índice e cache apagados;
- uma falha em um livro é exibida no painel e não interrompe os demais;
- PDFs com páginas sem texto são parcialmente indexados e sinalizados para o futuro OCR;
- DOCX é convertido por LibreOffice antes de passar pelo mesmo pipeline de PDF.

Os arquivos derivados ficam em `~/.local/share/rpg-rules-search/documents/`. Se o LibreOffice não estiver instalado, PDFs continuam funcionando e cada DOCX recebe uma mensagem de erro no relatório da sincronização.

## Ollama

O modo **Pergunta** recupera até oito páginas pelo FTS5 e só então envia esses trechos ao Ollama local. O prompt exige referências no formato `[Livro, p. X]` e instrui o modelo a declarar quando não há evidência suficiente.

O modo Pergunta usa somente páginas recuperadas dos livros. A aplicação não possui modo de ensinamento nem persiste contexto fornecido em perguntas.

Nas buscas e perguntas, o Ollama também prepara em background termos curtos e
sinônimos para ampliar recuperações futuras da mesma consulta. O FTS5 responde
imediatamente e continua sendo a fonte dos resultados; se o modelo estiver
indisponível, a busca original permanece inalterada. As expansões ficam somente
em memória, limitadas às 256 consultas mais recentes, e são descartadas ao
reiniciar a aplicação ou trocar a configuração do Ollama.

## Agente do projeto

O agente de workspace fica em `.github/agents/arquivo-arcano.agent.md`. Ele é gerado a partir de `.github/copilot-instructions.md` por `scripts/update_agent.py`; o hook `.github/hooks/update-agent.json` executa essa atualização no início de cada sessão de agente do VS Code.

A aplicação inicia o serviço local do Ollama quando necessário, seleciona separadamente o melhor modelo instalado para texto e para visão e, se não houver um compatível, instala `gemma3:latest` para texto e `gemma3:4b` para imagens. Para usar outros modelos ou um servidor Ollama em outro computador:

```bash
RPG_RULES_OLLAMA_MODEL=qwen3:latest \
RPG_RULES_OLLAMA_VISION_MODEL=gemma3:4b \
RPG_RULES_OLLAMA_URL=http://192.168.1.50:11434 \
.venv/bin/python -m rpg_rules_search
```

Em servidor remoto, a aplicação apenas usa a API configurada e não tenta iniciar processos no outro computador. Defina `RPG_RULES_OLLAMA_AUTO_PULL=0` para impedir downloads automáticos; nesse caso, ao menos um modelo compatível precisa estar instalado.

A mesma configuração pode ser alterada em **Configurar biblioteca > Servidor Ollama**. URL, modelos de texto e visão e a política de instalação automática ficam persistidos em `~/.local/share/rpg-rules-search/ollama.json` e são recarregados nas próximas execuções.

- `GET /api/ollama` informa configuração, disponibilidade, tipo de host e modelos instalados;
- `PUT /api/ollama` salva a configuração e passa a usá-la nas próximas perguntas e auto-tags;
- consultar ou salvar um host remoto não inicia processos nem instala modelos nele.

### Ollama em outro computador

O módulo `rpg_rules_search.remote_ollama` prepara um segundo computador sem executar comandos remotos ou copiar credenciais. No computador que executará os modelos, instale este projeto e rode:

```bash
# instala pelo instalador oficial no Linux, Homebrew no macOS ou winget no Windows
.venv/bin/python -m rpg_rules_search.remote_ollama install

# disponibiliza o Ollama na rede local
.venv/bin/python -m rpg_rules_search.remote_ollama serve --host 0.0.0.0 --port 11434
```

Restrinja a porta `11434` no firewall aos endereços da sua rede local. O Ollama não deve ser publicado diretamente na internet.

Neste computador, configure o endereço do servidor pela interface ou pela CLI e reinicie a aplicação:

```bash
.venv/bin/python -m rpg_rules_search.remote_ollama configure-client \
	http://192.168.1.50:11434
```

O Arquivo Arcano detectará os modelos instalados no servidor. Quando `auto_pull` estiver ativo, modelos ausentes também serão baixados nesse servidor pela API do Ollama.

### MCP de desenvolvimento com Ollama

O workspace registra `arquivo-arcano-ollama` em `.vscode/mcp.json`. O VS Code inicia o servidor por `stdio` durante sessões neste projeto e oferece a ferramenta `consultar_ollama_do_projeto` para uma segunda revisão local de código.

O MCP aceita no máximo 12 arquivos textuais e 120 KB por consulta. Ele rejeita caminhos fora do repositório, `.git`, `.venv`, `node_modules`, `.env`, `credentials.json` e `credencials.json`. A resposta do modelo é apenas uma sugestão: testes e código continuam sendo a fonte de verdade.

Depois de instalar as dependências de desenvolvimento com `pip install -e '.[dev]'`, abra a lista de servidores MCP do VS Code, inicie `arquivo-arcano-ollama` e confirme a confiança no servidor local deste repositório.

## Biblioteca de imagens

O modo **Ícone** permite armazenar imagens locais na biblioteca e pesquisar por tags. Imagens PNG, JPEG, WEBP e GIF encontradas na pasta local ou pasta do Drive configurada são sincronizadas automaticamente junto com os livros.

- Upload aceita PNG, JPEG, WEBP e GIF (até 8 MB por arquivo);
- deduplicação por SHA-256 evita cadastrar o mesmo arquivo duas vezes;
- nomes de arquivo geram tags iniciais e o modelo de visão local adiciona tags visuais quando disponível;
- alterações e remoções na origem são refletidas nas sincronizações seguintes;
- tags podem ser geradas automaticamente por IA local (Ollama) ou editadas via API;
- busca de imagens usa FTS5 sobre tags no SQLite.

Endpoints principais:

- `POST /api/images?auto_tag=true` para upload;
- `GET /api/images?q=...` para busca/listagem;
- `PUT /api/images/{id}/tags` para ajuste manual;
- `POST /api/images/auto-tag` para auto-tag em lote de imagens sem tags.

Scripts locais:

```bash
# taguear apenas imagens sem tags
python3 scripts/auto_tag_images.py --database ~/.local/share/rpg-rules-search/library.sqlite3 --limit 200

# reprocessar tags de todas as imagens
python3 scripts/retag_images.py --database ~/.local/share/rpg-rules-search/library.sqlite3 --limit 500
```

## Exportação portátil

`GET /api/export` baixa `arquivo-arcano.zip`, uma base independente de Ollama ou de qualquer provedor de IA. O arquivo contém:

- `manifest.json`, com versão do formato e contagens;
- `pages.jsonl`, com livro, SHA-256, página, citação, categoria e textos bruto e normalizado;
- `images.jsonl`, com metadados, SHA-256, tags e origem das tags;
- `images/`, com os binários disponíveis dos ativos deduplicados.

Somente documentos e imagens completamente processados (`ready`) são exportados. O ZIP é criado localmente para cada download e removido após a resposta.

## Fichas de ameaças

Durante a ingestão, páginas com marcadores de ficha como Defesa, Pontos de Vida, atributos, resistências e ações são classificadas como ameaças. Páginas que também contêm o cabeçalho "Ameaças da Realidade" recebem essa categoria e podem ser filtradas no modo **Ameaças**.

Nunca adicione `credentials.json`, tokens OAuth, PDFs, DOCX, páginas renderizadas, OCR ou embeddings ao repositório.

## Privacidade e conteúdo

O projeto não inclui livros. Use somente documentos que você possui ou está autorizado a processar. Originais e derivados permanecem no computador, sem links públicos ou redistribuição.
