# Deploy remoto

## 1. Preparar o Raspberry Pi

O instalador prepara as dependências, o Python 3.12 e o serviço automaticamente.

## 2. Expor a aplicação localmente

O serviço usa `RPG_RULES_HOST=0.0.0.0` e `RPG_RULES_PORT=8765`, então a aplicação fica disponível na rede local do Pi em:

```text
http://<IP-DO-RPI>:8765
```

## 3. Instalar e rodar como serviço

No Pi, rode:

```bash
curl -fsSL https://raw.githubusercontent.com/HiltonWS/rpg-rules-search/main/scripts/deploy.sh | bash
```

O script identifica o usuário conectado mesmo quando precisa usar `sudo`. Se o Pi
estiver sendo administrado diretamente como `root` e não houver um usuário comum,
informe-o explicitamente:

```bash
curl -fsSL https://raw.githubusercontent.com/HiltonWS/rpg-rules-search/main/scripts/deploy.sh \
  | USER_NAME=seu_usuario bash
```

Para atualizar posteriormente, execute o mesmo comando. O repositório será atualizado
e o serviço reiniciado.

## 4. Segurança

- Não exponha a porta 8765 diretamente na internet.
- Prefira SSH tunneling para acesso remoto:

```bash
ssh -L 8765:127.0.0.1:8765 rspi5@<IP-DO-RPI>
```

Depois abra no seu computador:

```text
http://127.0.0.1:8765
```

## 5. Conectar ao Google Drive

O Google aceita clientes OAuth do tipo **App para computador** somente com callback
em um endereço de loopback. Por isso, não inicie a conexão do Drive pela página
`http://<IP-DO-RPI>:8765`: o callback não pode usar o IP bruto do Raspberry Pi.

1. Abra o túnel SSH mostrado acima.
2. Acesse `http://127.0.0.1:8765` no mesmo computador em que o túnel está aberto.
3. Em **Configurar biblioteca**, carregue a credencial OAuth e clique em
  **Conectar ao Google Drive**.

Depois da autorização, o token fica armazenado no Raspberry Pi e o uso normal da
aplicação pode voltar a ser feito por `http://<IP-DO-RPI>:8765` na rede local.

## 6. Ollama local

O instalador habilita o `ollama.service` no boot, configura reinício automático,
limita a API a `127.0.0.1:11434` e baixa `gemma3:1b`, um modelo de texto mais
adequado à memória do Raspberry Pi. Downloads automáticos ficam desativados no
serviço para evitar que o modelo de visão de 4B seja baixado durante a primeira
inicialização. Um Ollama remoto continua disponível apenas quando configurado
explicitamente pela interface.

Em uma instalação existente, execute novamente o instalador para aplicar essa
configuração:

```bash
curl -fsSL https://raw.githubusercontent.com/HiltonWS/rpg-rules-search/main/scripts/deploy.sh \
  | bash
```

Verifique os dois serviços e a API local do Ollama com:

```bash
systemctl status ollama rpg-rules-search --no-pager
ollama list
curl -fsS http://127.0.0.1:11434/api/tags
```

Para acompanhar uma pergunta que não respondeu:

```bash
journalctl -u ollama -u rpg-rules-search -n 100 --no-pager
free -h
```

O modelo de visão `gemma3:4b` é opcional e pode ser instalado manualmente em um
Raspberry com memória suficiente usando `ollama pull gemma3:4b`.

## 7. Atualizações automáticas

O instalador também habilita `rpg-rules-search-update.timer`. A cada 15 minutos,
com um pequeno atraso aleatório, ele consulta a branch `main` no GitHub. Quando há
um novo commit, o atualizador aceita apenas avanço rápido, reinstala as
dependências, reaplica as units do systemd, garante que o Ollama esteja ativo e
reinicia o Arquivo Arcano.

A atualização é cancelada se houver alterações locais versionadas ou se a branch
local tiver divergido. Esse bloqueio evita apagar trabalho feito diretamente no
Raspberry Pi.

Verifique ou execute a atualização manualmente com:

```bash
systemctl status rpg-rules-search-update.timer --no-pager
systemctl list-timers rpg-rules-search-update.timer --no-pager
sudo systemctl start rpg-rules-search-update.service
journalctl -u rpg-rules-search-update.service -n 100 --no-pager
```
