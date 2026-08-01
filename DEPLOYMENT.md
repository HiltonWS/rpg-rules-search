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
