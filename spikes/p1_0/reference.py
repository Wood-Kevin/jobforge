"""Reference labels for the model evaluation spike.

Authored by Claude on 2026-10-21 from the posting text, BEFORE any model output
was produced. These are not the golden-set labels.

STATUS: PROVISIONAL (Kevin, Q27, 2026-10-21): usable for testing and model
comparison only. No item enters the golden set until every model miss against
it has been independently reviewed (reviewer did not author the label; sees
posting text, label and model output; records label correct / model correct /
ambiguous) and Kevin confirms it. See docs/ARCHITECTURE.md 6.7.

PROBES: real postings. Each probe is an anchor (a distinctive substring of
the posting) plus the labels a correct extraction should carry.
  expect: dict of field -> allowed values (set) or exact value
  extracted: True  -> a correct model must extract an item overlapping the anchor
TRAPS: text that must NOT come back as a candidate requirement
  (subject=candidate, polarity=affirmative, importance in {hard, required}).
SNIPPETS: the ARCHITECTURE 6.6 layer-A cases as tiny postings.
  kind "target": an item overlapping the anchor must exist with the labels.
  kind "not_requirement": no candidate requirement may overlap the anchor.
  kind "info": recorded, not scored (genuinely ambiguous by design).
"""

HARD_OR_REQ = {"hard", "required"}

PROBES = {
    "greenhouse_gitlab_8859063002": {
        "probes": [
            {"anchor": "you must be a United States Citizen", "expect": {"importance": {"hard"}, "polarity": {"affirmative"}, "subject": {"candidate"}, "eligibility_type": {"work_authorization"}}},
            {"anchor": "Strong Linux systems administration knowledge", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Ability to use scripting languages (preferably Ruby or Bash)", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Practical experience with Git and continuous integration", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Familiarity with DevOps practices and container-orchestration", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "overnight shift role with working hours of 12:00am - 7:00am EST", "expect": {"importance": HARD_OR_REQ, "subject": {"candidate"}}},
        ],
        "traps": [
            "regardless of race, color, religion, ancestry",
            "national origin, age, citizenship, marital status",
            "protected veteran status",
            "many successful candidates do not meet every single requirement",
            "More than 50 million registered users",
        ],
    },
    "greenhouse_axonius_8009643003": {
        "probes": [
            # No current eligibility_type fits "active-duty SkillBridge participant"; only importance scored.
            {"anchor": "Active-duty military service member approved to participate in the DoD SkillBridge program", "expect": {"importance": {"hard"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Operational background in military IT, communications", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Working knowledge of networking fundamentals", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "One or more industry certifications such as CompTIA Security+", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "scripting languages such as Python is a plus", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
        ],
        "traps": [
            "without regard to race, color, ancestry",
            "citizenship, marital status, disability, or Veteran status",
            "Axonius participates in E-Verify",
            "supports millions of assets for leading customers",
        ],
    },
    "ashby_vultr_24c3445f-27b2-40fb-870e-e59b71305787": {
        "probes": [
            {"anchor": "accepting applications from candidates residing in the following states", "expect": {"importance": {"hard"}, "polarity": {"affirmative"}, "subject": {"candidate"}, "eligibility_type": {"residency"}}},
            {"anchor": "Strong proficiency administering Linux systems", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Familiarity with scripting languages such as Bash, Python", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            # "is a plus" under a "Qualifications" heading: wording beats heading.
            {"anchor": "Experience with virtualization, networking, or software development is a plus", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Distributed storage solutions (e.g., Ceph)", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
        ],
        "traps": [
            "Professional Development Reimbursement of $2,500",
            "33 global cloud data center locations",
            "disability, veteran status, or any other protected status",
            "will not conduct a background check until after an offer",
        ],
    },
    "greenhouse_huntress_7743188003": {
        "probes": [
            {"anchor": "4+ years of experience in a SOC or Digital Forensics (DFIR) role", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}, "min_years": 4}},
            {"anchor": "Working knowledge of Windows Administration", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "static and dynamic malware analysis concepts", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Previous experience in an MSP/MSSP/MDR role", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Experience with scripting languages (such as PowerShell", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
        ],
        "info": ["Location: Remote, US (West Coast)"],  # residence or shift? ambiguous, record only
        "traps": [
            "Founded in 2015 by former NSA cyber operators",
            "12 weeks of paid parental leave",
            "disability, veteran status, genetic information",
            "We do discriminate against hackers",
        ],
    },
    "greenhouse_keepersecurity_4459433009": {
        "probes": [
            {"anchor": "Must reside within reasonable commuting distance of Keeper", "expect": {"importance": {"hard"}, "polarity": {"affirmative"}, "subject": {"candidate"}, "eligibility_type": {"residency"}}},
            {"anchor": "Ability to lift up to 50 pounds", "expect": {"importance": {"hard"}, "polarity": {"affirmative"}, "subject": {"candidate"}, "eligibility_type": {"physical"}}},
            {"anchor": "3+ years of experience in IT support", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}, "min_years": 3}},
            {"anchor": "Strong hands-on experience supporting Microsoft Windows and macOS", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Familiarity with virtualization technologies such as VMware or Hyper-V", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Relevant CompTIA, Microsoft, networking or IT support certifications", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
        ],
        "traps": [
            "Employment history, qualifications, work eligibility",
            "participant in the U.S. Federal E-Verify program",
            "disability status and veteran status (US)",
            "Background verification (post offer)",
            "Above market annual bonuses",
        ],
    },
    "greenhouse_tailscale_4710785005": {
        "probes": [
            {"anchor": "Prior Go expertise is not a requirement", "expect": {"polarity": {"negated"}}},
            {"anchor": "Four or more years of experience in software development", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}, "min_years": 4}},
            {"anchor": "Proficiency in C# and modern native UI frameworks", "expect": {"importance": {"required"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Experience with WinUI 3 and Windows App SDK", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
            {"anchor": "Proficiency in Go", "expect": {"importance": {"preferred"}, "polarity": {"affirmative"}, "subject": {"candidate"}}},
        ],
        "traps": [
            "meet 100% of the qualifications",
            "$1500 USD annually for professional development",
            "Founded in 2019 and fully distributed",
            "Some roles require in-office collaboration",
        ],
    },
}

# ARCHITECTURE 6.6 layer A as tiny postings: (id, area, text, kind, anchor, expect)
SNIPPETS = [
    ("neg_cloud", "negation", "Requirements:\n- No prior cloud experience required.", "target", "No prior cloud experience required", {"polarity": {"negated"}}),
    ("neg_degree", "negation", "Qualifications:\n- A degree is not required.", "target", "A degree is not required", {"polarity": {"negated"}}),
    ("neg_k8s", "negation", "What you'll need:\n- You don't need Kubernetes experience on day one; we'll train you.", "target", "You don't need Kubernetes experience", {"polarity": {"negated"}}),
    ("neg_mixed_clearance", "negation", "Requirements:\n- Clearance not required, but must be able to obtain a Secret clearance.", "target", "must be able to obtain a Secret clearance", {"importance": {"required"}, "polarity": {"affirmative"}, "eligibility_type": {"clearance"}}),
    ("imp_ccna_req", "importance", "Requirements:\n- CCNA certification required.", "target", "CCNA certification required", {"importance": {"required"}, "polarity": {"affirmative"}}),
    ("imp_ccna_plus", "importance", "Nice to have:\n- CCNA certification is a plus.", "target", "CCNA certification is a plus", {"importance": {"preferred"}}),
    ("imp_ccna_equiv", "importance", "Requirements:\n- CCNA or equivalent experience.", "target", "CCNA or equivalent experience", {"importance": {"required"}, "polarity": {"affirmative"}}),
    ("imp_conflict_aws", "importance", "Nice to have:\n- Must have AWS experience.", "info", "Must have AWS experience", {}),
    ("imp_degree_or_years", "importance", "Requirements:\n- Bachelor's degree or 4 additional years of relevant experience.", "target", "Bachelor's degree or 4 additional years", {"importance": {"required"}, "polarity": {"affirmative"}}),
    ("subj_customers", "subject", "About us:\nOur customers include Fortune 500 banks.", "not_requirement", "Our customers include Fortune 500 banks", {}),
    ("subj_stack", "subject", "About the team:\nOur stack: Terraform, Ansible, Datadog.", "not_requirement", "Our stack: Terraform, Ansible, Datadog", {}),
    ("subj_mentor_years", "subject", "Responsibilities:\n- You'll mentor engineers with 10+ years of experience.", "not_requirement_years", "mentor engineers with 10+ years", {}),
    ("subj_benefit_cert", "subject", "Benefits:\n- We pay for AWS certifications.", "not_requirement", "We pay for AWS certifications", {}),
    ("eeo", "eeo", "Equal opportunity:\nWe do not discriminate on the basis of race, religion, citizenship, or veteran status.", "not_requirement", "race, religion, citizenship, or veteran status", {}),
    ("elig_citizen", "eligibility", "Requirements:\n- Must be a US citizen.", "target", "Must be a US citizen", {"importance": {"hard"}, "eligibility_type": {"work_authorization"}}),
    ("elig_tssci", "eligibility", "Requirements:\n- Active TS/SCI clearance required.", "target", "Active TS/SCI clearance required", {"importance": {"hard"}, "eligibility_type": {"clearance"}}),
    ("elig_reside", "eligibility", "Requirements:\n- Must reside in Northern California.", "target", "Must reside in Northern California", {"importance": {"hard"}, "eligibility_type": {"residency"}}),
    ("elig_sponsor", "eligibility", "Requirements:\n- We are unable to sponsor visas for this role.", "target", "unable to sponsor visas", {"importance": {"hard"}, "eligibility_type": {"sponsorship", "work_authorization"}}),
    ("elig_lift", "eligibility", "Requirements:\n- Ability to lift 50 lbs.", "target", "Ability to lift 50 lbs", {"importance": {"hard"}, "eligibility_type": {"physical"}}),
    ("elig_travel", "eligibility", "Requirements:\n- Willingness to travel up to 25%.", "target", "travel up to 25%", {"importance": HARD_OR_REQ, "eligibility_type": {"travel"}}),
    ("years_5", "years", "Requirements:\n- 5+ years of Linux administration experience.", "target", "5+ years of Linux administration", {"importance": {"required"}, "min_years": 5}),
    ("years_pref", "years", "Requirements:\n- 3-5 years of experience preferred.", "target", "3-5 years of experience preferred", {"importance": {"preferred"}, "min_years": 3}),
]
