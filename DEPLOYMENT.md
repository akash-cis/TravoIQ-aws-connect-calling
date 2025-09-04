## TravoIQ Deployment Guide

### SSH Access

Use the following command to connect to the EC2 instance:

```bash
ssh -i travoiq-bastion.pem ubuntu@54.187.15.1
```

If you encounter a permission error for the key on Unix-like shells, set restrictive permissions:

```bash
chmod 400 travoiq-bastion.pem
```

### EC2 Details

- IP: 54.187.15.1
- Port: 22
- Username: ubuntu
- Key file: travoiq-bastion.pem

### Code Location on Server

- Base path: /var/www/html/travoiq/

### Deployment Commands (run on EC2)

- Frontend:

```bash
/home/script/stage.sh FE
```

- Backend:

```bash
/home/script/stage.sh BE
```

### Deploy Backend: Quick Steps

1. SSH into the EC2 instance:

```bash
ssh -i travoiq-bastion.pem ubuntu@54.187.15.1
```

2. Run the backend stage deployment script:

```bash
/home/script/stage.sh BE
```

### Copy a Local File to EC2 (SCP)

Run from your local machine to copy `app/main.py` to the server path used by stage backend:

```bash
scp -i travoiq-bastion.pem "D:\CREST\TravoIQ\app\main.py" ubuntu@54.187.15.1:/var/www/html/travoiq/stage/BE/app/main.py
```

### Troubleshooting

- Permission denied (publickey): ensure the key has correct permissions (see `chmod 400` above) and that you referenced the correct `.pem` file.
- No such file or directory on SCP: verify the destination path exists on the server; adjust the path if your project layout differs.
- Permission denied running script: ensure the script is executable. If needed, on EC2 run: `sudo chmod +x /home/script/stage.sh`.