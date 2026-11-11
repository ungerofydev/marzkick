"""add hysteria2 proxy type

Revision ID: a1f3c0de9b27
Revises: 2b231de97dc3
Create Date: 2026-11-11 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1f3c0de9b27'
down_revision = '2b231de97dc3'
branch_labels = None
depends_on = None

OLD = ('VMess', 'VLESS', 'Trojan', 'Shadowsocks')
NEW = OLD + ('Hysteria2',)


def upgrade() -> None:
    # SQLite stores the enum as plain VARCHAR without a CHECK constraint, so only
    # MySQL/MariaDB (native ENUM column) needs altering.
    if op.get_bind().dialect.name in ('mysql', 'mariadb'):
        op.alter_column('proxies', 'type',
                        existing_type=sa.Enum(*OLD, name='proxytypes'),
                        type_=sa.Enum(*NEW, name='proxytypes'),
                        existing_nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name in ('mysql', 'mariadb'):
        op.execute("DELETE FROM proxies WHERE type = 'Hysteria2'")
        op.alter_column('proxies', 'type',
                        existing_type=sa.Enum(*NEW, name='proxytypes'),
                        type_=sa.Enum(*OLD, name='proxytypes'),
                        existing_nullable=False)
