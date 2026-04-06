"""
Django management command to fully bootstrap a local development environment.

Runs setup_tenants, seeds intelligence data, ingests regulatory text,
verifies policy packs load, then prints a summary of everything that was set up.

Usage:
    python manage.py setup_dev
    python manage.py setup_dev --skip-seeds
    python manage.py setup_dev --skip-policies
    python manage.py setup_dev --skip-policy-check
    python manage.py setup_dev --tenant-name "My Company" --tenant-subdomain myco \
        --owner-email owner@myco.com --owner-password secret123
"""
from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = 'Bootstrap the local dev environment: tenants, seeds, policy ingest, and policy packs in one command'

    def add_arguments(self, parser):
        # Pass-through args for setup_tenants
        parser.add_argument('--tenant-name', type=str, help='Name of a custom tenant to create')
        parser.add_argument('--tenant-subdomain', type=str, help='Subdomain/slug for the custom tenant')
        parser.add_argument('--schema-name', type=str, default=None, help='Schema name for the custom tenant')
        parser.add_argument('--owner-email', type=str, help='Email of the custom tenant owner')
        parser.add_argument('--owner-password', type=str, help='Password for the custom tenant owner')
        parser.add_argument('--skip-sample', action='store_true', help='Skip creating sample tenants')

        # setup_dev-specific flags
        parser.add_argument(
            '--skip-seeds',
            action='store_true',
            help='Skip seed_recommendations (useful for faster re-runs when seeds are already loaded)',
        )
        parser.add_argument(
            '--skip-policies',
            action='store_true',
            help='Skip policy ingestion steps (fetch + tag TX/NM rules) for faster re-runs when policies are already loaded',
        )
        parser.add_argument(
            '--skip-policy-check',
            action='store_true',
            help='Skip test_policy_loader verification',
        )

    def handle(self, *args, **options):
        seeds_loaded = False
        policy_ok = None  # None = not checked, True = passed, False = failed
        policies_ingested = {}  # track per-substep status

        # ------------------------------------------------------------------ #
        # Step 1 — Tenants                                                    #
        # ------------------------------------------------------------------ #
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.WARNING('Step 1/4  Setting up tenants...'))
        self.stdout.write('=' * 60)

        setup_tenants_kwargs = {}
        passthrough_keys = ('tenant_name', 'tenant_subdomain', 'schema_name',
                            'owner_email', 'owner_password', 'skip_sample')
        for key in passthrough_keys:
            if options.get(key) is not None:
                setup_tenants_kwargs[key] = options[key]

        call_command('setup_tenants', **setup_tenants_kwargs)

        # ------------------------------------------------------------------ #
        # Step 2 — Intelligence seeds                                         #
        # ------------------------------------------------------------------ #
        self.stdout.write('\n' + '=' * 60)
        if options['skip_seeds']:
            self.stdout.write(self.style.WARNING('Step 2/4  Skipping intelligence seeds (--skip-seeds)'))
            self.stdout.write('=' * 60)
        else:
            self.stdout.write(self.style.WARNING('Step 2/4  Loading intelligence seeds...'))
            self.stdout.write('=' * 60)
            try:
                call_command('seed_recommendations')
                seeds_loaded = True
                self.stdout.write(self.style.SUCCESS('  Rejection patterns and recommendations loaded'))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f'  WARNING: seed_recommendations failed (non-fatal): {exc}'
                ))

        # ------------------------------------------------------------------ #
        # Step 3 — Policy ingestion (scrapes external regulatory websites)   #
        # ------------------------------------------------------------------ #
        self.stdout.write('\n' + '=' * 60)
        if options['skip_policies']:
            self.stdout.write(self.style.WARNING('Step 3/4  Skipping policy ingestion (--skip-policies)'))
            self.stdout.write('=' * 60)
        else:
            self.stdout.write(self.style.WARNING('Step 3/4  Ingesting regulatory text (requires internet)...'))
            self.stdout.write('=' * 60)

            # 3a — Fetch TX Chapter 3 rules
            self.stdout.write('  Fetching TX Chapter 3 rules from law.cornell.edu...')
            try:
                call_command('fetch_tx_ch3', write=True)
                policies_ingested['fetch_tx_ch3'] = True
                self.stdout.write(self.style.SUCCESS('  TX Chapter 3 rules fetched'))
            except Exception as exc:
                policies_ingested['fetch_tx_ch3'] = False
                self.stdout.write(self.style.WARNING(
                    f'  WARNING: fetch_tx_ch3 failed (non-fatal): {exc}'
                ))

            # 3b — Tag TX rules
            self.stdout.write('  Tagging TX rules with jurisdiction and topics...')
            try:
                call_command('tag_tx_ch3')
                policies_ingested['tag_tx_ch3'] = True
                self.stdout.write(self.style.SUCCESS('  TX rules tagged'))
            except Exception as exc:
                policies_ingested['tag_tx_ch3'] = False
                self.stdout.write(self.style.WARNING(
                    f'  WARNING: tag_tx_ch3 failed (non-fatal): {exc}'
                ))

            # 3c — Fetch NM OCD rules
            self.stdout.write('  Fetching NM OCD rules from srca.nm.gov...')
            try:
                call_command('fetch_nm_ocd', write=True)
                policies_ingested['fetch_nm_ocd'] = True
                self.stdout.write(self.style.SUCCESS('  NM OCD rules fetched'))
            except Exception as exc:
                policies_ingested['fetch_nm_ocd'] = False
                self.stdout.write(self.style.WARNING(
                    f'  WARNING: fetch_nm_ocd failed (non-fatal): {exc}'
                ))

            # 3d — Tag NM rules
            self.stdout.write('  Tagging NM rules with jurisdiction and topics...')
            try:
                call_command('tag_nm_ocd')
                policies_ingested['tag_nm_ocd'] = True
                self.stdout.write(self.style.SUCCESS('  NM rules tagged'))
            except Exception as exc:
                policies_ingested['tag_nm_ocd'] = False
                self.stdout.write(self.style.WARNING(
                    f'  WARNING: tag_nm_ocd failed (non-fatal): {exc}'
                ))

        # ------------------------------------------------------------------ #
        # Step 4 — Policy pack verification                                   #
        # ------------------------------------------------------------------ #
        self.stdout.write('\n' + '=' * 60)
        if options['skip_policy_check']:
            self.stdout.write(self.style.WARNING('Step 4/4  Skipping policy pack check (--skip-policy-check)'))
            self.stdout.write('=' * 60)
        else:
            self.stdout.write(self.style.WARNING('Step 4/4  Verifying policy packs load...'))
            self.stdout.write('=' * 60)
            try:
                call_command('test_policy_loader')
                policy_ok = True
                self.stdout.write(self.style.SUCCESS('  Policy packs verified'))
            except Exception as exc:
                policy_ok = False
                self.stdout.write(self.style.WARNING(
                    f'  WARNING: test_policy_loader failed (non-fatal): {exc}'
                ))

        # ------------------------------------------------------------------ #
        # Summary                                                              #
        # ------------------------------------------------------------------ #
        from apps.policy_ingest.models import PolicyRule  # local import — optional app
        try:
            policy_rule_count = PolicyRule.objects.count()
        except Exception:
            policy_rule_count = None

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('  Dev environment ready!'))
        self.stdout.write('=' * 60)

        self.stdout.write('\nTenants created:')
        self.stdout.write(self.style.SUCCESS('  public   — localhost:8001'))
        if not options.get('skip_sample'):
            self.stdout.write(self.style.SUCCESS('  demo     — demo.localhost:8001'))
            self.stdout.write(self.style.SUCCESS('  test     — test.localhost:8001'))
        if options.get('tenant_name'):
            subdomain = options.get('tenant_subdomain', '?')
            self.stdout.write(self.style.SUCCESS(f'  {subdomain}  — {subdomain}.localhost:8001'))

        self.stdout.write('\nCredentials:')
        self.stdout.write('  admin@localhost        / admin123   (superuser, localhost:8001/admin/)')
        if not options.get('skip_sample'):
            self.stdout.write('  demo@example.com       / demo123    (demo tenant owner)')
            self.stdout.write('  test@example.com       / test123    (test tenant owner)')
        if options.get('owner_email') and options.get('owner_password'):
            self.stdout.write(
                f'  {options["owner_email"]:<22} / {options["owner_password"]}'
            )

        self.stdout.write('\nIntelligence seeds:')
        if options['skip_seeds']:
            self.stdout.write(self.style.WARNING('  skipped (--skip-seeds)'))
        elif seeds_loaded:
            self.stdout.write(self.style.SUCCESS('  TX W-3A rejection patterns and recommendations loaded'))
        else:
            self.stdout.write(self.style.ERROR('  failed — run seed_recommendations manually'))

        self.stdout.write('\nRegulatory text (PolicyRule records):')
        if options['skip_policies']:
            self.stdout.write(self.style.WARNING('  skipped (--skip-policies)'))
        else:
            step_labels = {
                'fetch_tx_ch3': 'TX Chapter 3 fetch',
                'tag_tx_ch3':   'TX Chapter 3 tag',
                'fetch_nm_ocd': 'NM OCD fetch',
                'tag_nm_ocd':   'NM OCD tag',
            }
            for key, label in step_labels.items():
                status = policies_ingested.get(key)
                if status is True:
                    self.stdout.write(self.style.SUCCESS(f'  {label}: OK'))
                elif status is False:
                    self.stdout.write(self.style.WARNING(f'  {label}: FAILED (non-fatal)'))
            if policy_rule_count is not None:
                self.stdout.write(self.style.SUCCESS(f'  Total PolicyRule records: {policy_rule_count}'))
            else:
                self.stdout.write(self.style.WARNING('  Could not query PolicyRule count'))

        self.stdout.write('\nPolicy packs:')
        if options['skip_policy_check']:
            self.stdout.write(self.style.WARNING('  check skipped (--skip-policy-check)'))
        elif policy_ok is True:
            self.stdout.write(self.style.SUCCESS('  TX W-3A  (tx_rrc_w3a_base_policy_pack.yaml)'))
            self.stdout.write(self.style.SUCCESS('  NM C-103 (nm_ocd_c103_base_policy_pack.yaml)'))
        elif policy_ok is False:
            self.stdout.write(self.style.WARNING('  verification failed — plans may still work, check logs'))
        else:
            self.stdout.write('  not checked')

        self.stdout.write('\nServices & ports:')
        self.stdout.write('  API       http://localhost:8001')
        self.stdout.write('  Admin     http://localhost:8001/admin/')
        self.stdout.write('  Flower    http://localhost:5555')
        self.stdout.write('  DB        localhost:5433  (user: postgres / postgres)')

        self.stdout.write('\nFrontend:')
        self.stdout.write('  CORS pre-configured for localhost:3000 and localhost:5173')
        self.stdout.write('  Start frontend: cd v0-regul-agent && npm run dev')

        self.stdout.write('\nDomain routing (/etc/hosts — optional):')
        self.stdout.write('  Add: 127.0.0.1  demo.localhost  test.localhost')
        self.stdout.write('  Then requests to demo.localhost:8001 route to the demo tenant')

        self.stdout.write('\nGet a JWT token:')
        self.stdout.write(
            '  curl -s -X POST http://localhost:8001/api/token/ \\\n'
            '       -H "Content-Type: application/json" \\\n'
            '       -d \'{"email":"demo@example.com","password":"demo123"}\''
        )

        self.stdout.write('\n' + '=' * 60 + '\n')
