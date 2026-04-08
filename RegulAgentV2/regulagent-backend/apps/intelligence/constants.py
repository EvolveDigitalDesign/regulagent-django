ISSUE_CATEGORIES = {
    'terminology': {
        'label': 'Terminology',
        'subcategories': {
            'naming_convention': 'Naming Convention',
            'abbreviation': 'Abbreviation',
            'unit_label': 'Unit Label',
        }
    },
    'precision': {
        'label': 'Precision',
        'subcategories': {
            'rounding': 'Rounding Error',
            'significant_figures': 'Significant Figures',
            'measurement_unit': 'Measurement Unit',
        }
    },
    'compliance': {
        'label': 'Compliance',
        'subcategories': {
            'missing_requirement': 'Missing Requirement',
            'incorrect_method': 'Incorrect Method',
            'exceeded_limit': 'Exceeded Limit',
        }
    },
    'documentation': {
        'label': 'Documentation',
        'subcategories': {
            'missing_narrative': 'Missing Narrative',
            'incomplete_description': 'Incomplete Description',
            'missing_formation': 'Missing Formation Name',
        }
    },
    'calculation': {
        'label': 'Calculation',
        'subcategories': {
            'volume_error': 'Volume Calculation Error',
            'depth_error': 'Depth Calculation Error',
            'material_error': 'Material Quantity Error',
        }
    },
    'formatting': {
        'label': 'Formatting',
        'subcategories': {
            'date_format': 'Date Format',
            'number_format': 'Number Format',
            'text_format': 'Text Format',
        }
    },
}

AGENCY_CHOICES = [('RRC', 'Texas RRC'), ('NMOCD', 'New Mexico OCD')]
FORM_TYPE_CHOICES = [('w3', 'W-3'), ('w3a', 'W-3A'), ('c103', 'C-103'), ('c104', 'C-104'), ('c105', 'C-105')]

FILING_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('under_review', 'Under Review'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('revision_requested', 'Revision Requested'),
    ('deficiency', 'Deficiency Notice'),
]

PARSE_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('parsed', 'AI Parsed'),
    ('verified', 'User Verified'),
]

RECOMMENDATION_SCOPE_CHOICES = [
    ('cross_tenant', 'Cross-Tenant Pattern'),
    ('tenant', 'Tenant-Specific'),
    ('cold_start', 'Cold-Start Rule'),
]

PRIORITY_CHOICES = [
    ('high', 'High'),
    ('medium', 'Medium'),
    ('low', 'Low'),
]

INTERACTION_ACTION_CHOICES = [
    ('shown', 'Shown'),
    ('accepted', 'Accepted'),
    ('dismissed', 'Dismissed'),
    ('snoozed', 'Snoozed'),
]

FILING_SOURCE_CHOICES = [
    ('synced', 'Synced from Portal'),
    ('submitted', 'Submitted via Platform'),
    ('manual', 'Manually Created'),
]
