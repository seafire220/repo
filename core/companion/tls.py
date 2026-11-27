"""The device's own HTTPS, for the live camera on a phone (docs/26-animation-director.md, "Live from a phone").

A phone's browser lets a page use its camera live only over a secure connection, and a device on a home
network has no certificate a phone already trusts. So the device makes its own (self-signed), the first
time the owner turns the live camera on: phones warn about it once, and the owner taps past the warning
(the settings page says how, and why). It is made to the rules phones apply to every server certificate
(Apple's: at most 825 days, the names in subjectAltName, for server authentication, SHA-256, P-256), and
made again a month before it runs out.

The key stays in the device's config folder, readable by the service alone; backups leave it out (a new
one is made when needed).
"""
import datetime
import hashlib
import os
import shutil
import ssl
import subprocess
import tempfile

DAYS = 825
RENEW_WITHIN = 30
CERT, KEY = "phone-cert.pem", "phone-key.pem"
NAMES = ("companion.local", "companion")


def _expires(cert):
    """When the certificate runs out (UTC), or None if it cannot be read."""
    try:
        out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", cert], capture_output=True,
                             text=True, timeout=20, check=True).stdout
        when = out.strip().split("=", 1)[1]
        return datetime.datetime.strptime(when, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def available():
    return shutil.which("openssl") is not None


def ensure(folder, addresses=(), names=NAMES, now=None):
    """The certificate and key, made (again) if missing or within a month of running out. Returns
    (cert path, key path, made now)."""
    cert, key = os.path.join(folder, CERT), os.path.join(folder, KEY)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if os.path.isfile(cert) and os.path.isfile(key):
        expires = _expires(cert)
        if expires and expires - now > datetime.timedelta(days=RENEW_WITHIN):
            return cert, key, False
    if not available():
        raise OSError("this device has no openssl to make its certificate")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    alt = ["DNS:%s" % n for n in names] + ["IP:%s" % a for a in addresses if a]
    staged = tempfile.mkdtemp(prefix=".tls-", dir=folder)
    try:
        new_cert, new_key = os.path.join(staged, CERT), os.path.join(staged, KEY)
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-sha256",
             "-days", str(DAYS), "-nodes", "-keyout", new_key, "-out", new_cert,
             "-subj", "/CN=Character device on your home network",
             "-addext", "subjectAltName=" + ",".join(alt),
             "-addext", "extendedKeyUsage=serverAuth",
             "-addext", "keyUsage=critical,digitalSignature",
             "-addext", "basicConstraints=critical,CA:FALSE"],
            capture_output=True, timeout=60, check=True)
        os.chmod(new_key, 0o600)
        os.replace(new_key, key)
        os.replace(new_cert, cert)
    except subprocess.CalledProcessError as error:
        raise OSError("openssl could not make the certificate: %s" % error.stderr.decode(errors="replace")[-300:])
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return cert, key, True


def fingerprint(cert):
    """The certificate's SHA-256 fingerprint, as phones show it (AB:CD:...)."""
    with open(cert, encoding="ascii") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def server_context(cert, key):
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context
