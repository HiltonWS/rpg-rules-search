# Deploy remoto

## 1. Preparar o Raspberry Pi

```bash
sudo apt-get update
sudo apt-get install -y openssh-server git python3-pip python3-venv
```

Crie uma chave SSH e adicione a chave pública ao arquivo `~/.ssh/authorized_keys` do usuário `rspi5`.

## 2. Expor a aplicação localmente

O serviço usa `RPG_RULES_HOST=0.0.0.0` e `RPG_RULES_PORT=8765`, então a aplicação fica disponível na rede local do Pi em:

```text
http://<IP-DO-RPI>:8765
```

## 3. Instalar e rodar como serviço

No Pi, rode:

```bash
REPO_URL=https://github.com/<usuario>/<repositorio>.git \
  curl -fsSL https://raw.githubusercontent.com/HiltonWS/rpg-rules-search/main/scripts/deploy.sh | bash
```

Ou, em um clone local do repositório:

```bash
bash scripts/deploy.sh
```

Se o URL do GitHub retornar 404, o problema é que o repositório informado não existe ou não é acessível. Nesse caso:

- crie o repositório no GitHub e use a URL correta, ou
- rode o script a partir de um checkout local do projeto, que ele usará automaticamente.

## 4. Atualização automática via GitHub

O workflow em `.github/workflows/deploy.yml` roda em cada push na branch `main` e tenta conectar ao Pi por SSH para atualizar o checkout e reiniciar o serviço.

### Secrets necessários no GitHub

- `RPI_HOST`
- `RPI_USER`
- `RPI_SSH_KEY`

## 5. Segurança

- Não exponha a porta 8765 diretamente na internet.
- Prefira SSH tunneling para acesso remoto:

```bash
ssh -L 8765:127.0.0.1:8765 rspi5@<IP-DO-RPI>
```

Depois abra no seu computador:

```text
http://127.0.0.1:8765
```
