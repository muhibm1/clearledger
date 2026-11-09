#!/usr/bin/env bash
# Builds Voight's episode environment on a fresh Kaggle GPU box: Postgres 16,
# Odoo 17 from source, the frozen company restored as the vtgolden template,
# and the training packages. Run from the unpacked bundle's repo folder.
#   bash voight/kaggle/setup.sh <bundle dir>
set -euo pipefail
BUNDLE="$1"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HOME_DIR="$REPO/.odoo"

# Postgres 16 from the PostgreSQL apt repository, so the dump restores into the
# same major version it came from.
apt-get -qq update
apt-get -qq install -y curl ca-certificates gnupg lsb-release > /dev/null
install -d /usr/share/postgresql-common/pgdg
curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  > /etc/apt/sources.list.d/pgdg.list
apt-get -qq update
apt-get -qq install -y postgresql-16 libpq-dev > /dev/null
pg_ctlcluster 16 main start
su postgres -c "psql -qc \"CREATE ROLE odoo LOGIN SUPERUSER PASSWORD 'odoo-local'\""
export PGHOST=localhost PGUSER=odoo PGPASSWORD=odoo-local

# Odoo 17, the same way scripts/odoo-local.sh installs it.
mkdir -p "$HOME_DIR/src" "$HOME_DIR/data/filestore"
curl -fsSL https://nightly.odoo.com/17.0/nightly/src/odoo_17.0.latest.tar.gz | tar xz -C "$HOME_DIR/src" --strip-components=1
grep -v -i -E "^python-ldap|^psycopg2" "$HOME_DIR/src/requirements.txt" > "$HOME_DIR/req.txt"
echo "psycopg2-binary==2.9.9" >> "$HOME_DIR/req.txt"
# Odoo 17 supports Python up to 3.12 and Kaggle's own Python has no ensurepip, so Odoo gets
# Python 3.11 from the deadsnakes PPA, the version the local runs use.
if ! command -v python3.11 > /dev/null; then
  apt-get -qq install -y software-properties-common > /dev/null
  add-apt-repository -y ppa:deadsnakes/ppa > /dev/null
  apt-get -qq update
fi
apt-get -qq install -y python3.11 python3.11-venv python3.11-dev > /dev/null
python3.11 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q -r "$HOME_DIR/req.txt"
"$HOME_DIR/venv/bin/pip" install -q --no-deps -e "$HOME_DIR/src"
cat > "$HOME_DIR/odoo.conf" <<CONF
[options]
db_host = $PGHOST
db_user = $PGUSER
admin_passwd = local-only
addons_path = $HOME_DIR/src/odoo/addons
data_dir = $HOME_DIR/data
CONF

# The frozen company, as the template every episode copies.
createdb vtgolden
gunzip -c "$BUNDLE/vtgolden.sql.gz" | psql -q -v ON_ERROR_STOP=1 -d vtgolden > /dev/null
tar xzf "$BUNDLE/vtgolden-filestore.tgz" -C "$HOME_DIR/data/filestore"

# Training packages, the versions the CPU runs used; torch is Kaggle's own CUDA build.
pip install -q transformers==4.46.3 peft==0.13.2 accelerate==1.1.1 "psycopg[binary]==3.2.3"
echo "setup done"
