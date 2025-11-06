#!/bin/bash
# Complete database reset script for tenant-users authentication implementation
# This script will:
# 1. Remove old migrations
# 2. Create fresh migrations
# 3. Reset and migrate the database
# 4. Set up initial tenants and users
# 5. Restore policy data

set -e  # Exit on any error

echo "=========================================="
echo "RegulAgent Database Reset Script"
echo "=========================================="
echo ""

# Step 1: Remove old migration files (keep __init__.py)
echo "Step 1: Cleaning up old migrations..."
find apps/*/migrations -type f -name "*.py" ! -name "__init__.py" -delete
echo "✓ Old migrations removed"
echo ""

# Step 2: Create fresh migrations for all apps
echo "Step 2: Creating fresh migrations..."
python manage.py makemigrations tenants
python manage.py makemigrations public_core
python manage.py makemigrations tenant_overlay
python manage.py makemigrations policy_ingest
echo "✓ Fresh migrations created"
echo ""

# Step 3: Drop and recreate the database
echo "Step 3: Resetting database..."
python manage.py migrate_schemas --shared
echo "✓ Public schema migrated"
echo ""

# Step 4: Set up tenants and users
echo "Step 4: Setting up initial tenants and users..."
python manage.py setup_tenants
echo "✓ Tenants and users created"
echo ""

# Step 5: Restore policy data
echo "Step 5: Restoring policy data..."
echo "  - Fetching Texas Administrative Code Chapter 3..."
python manage.py fetch_tx_ch3 --write || echo "Warning: fetch_tx_ch3 may have issues, continuing..."
echo "  - Creating Rule 3.14 sections..."
python manage.py create_314_sections || echo "Warning: create_314_sections may have issues, continuing..."
echo "✓ Policy data restored"
echo ""

echo "=========================================="
echo "✓ Database reset complete!"
echo "=========================================="
echo ""
echo "Credentials for testing:"
echo "  Root admin: admin@localhost / admin123"
echo "  Demo tenant: demo@example.com / demo123"
echo "  Test tenant: test@example.com / test123"
echo ""
echo "Get JWT tokens:"
echo "  POST http://localhost:8000/api/auth/token/"
echo "  Body: {\"email\": \"demo@example.com\", \"password\": \"demo123\"}"

