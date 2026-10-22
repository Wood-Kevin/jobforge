# P1.0b model evaluation (prompt e1-draft-2)

| Model | Mode | Valid JSON | Quotes found | Probes found | Probes fully correct | importance | polarity | subject | eligibility_type | min_years | Traps violated | Snippets ok | Posting s (median / max) | Snippet s (median) | Reasoning tokens (median) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| google/gemma-4-12b-qat | fast | 6/6 | 97/97 | 31/32 | 29/32 | 30/30 | 30/30 | 30/30 | 2/4 | 3/3 | 0/26 | 18/21 | 38.49 / 51.18 | 1.95 | 0 |
| openai/gpt-oss-20b | fast | 6/6 | 84/91 | 30/32 | 27/32 | 29/30 | 30/30 | 30/30 | 1/4 | 3/3 | 0/26 | 14/21 | 23.92 / 39.87 | 2.53 | 4 |
| openai/gpt-oss-20b | think | 6/6 | 84/91 | 30/32 | 27/32 | 29/30 | 30/30 | 30/30 | 1/4 | 3/3 | 0/26 | 14/21 | 23.66 / 39.06 | 2.53 | 4 |
| qwen/qwen3-30b-a3b | fast | 6/6 | 98/98 | 30/32 | 28/32 | 29/30 | 30/30 | 30/30 | 2/4 | 3/3 | 0/26 | 17/21 | 66.48 / 192.97 | 3.73 | 0 |
| qwen/qwen3-30b-a3b | think | 6/6 | 94/94 | 30/32 | 26/32 | 28/30 | 30/30 | 30/30 | 2/4 | 3/3 | 0/26 | 16/21 | 163.15 / 174.21 | 18.81 | 1183 |

Snippets by area:

| Model | Mode | eeo | eligibility | importance | negation | subject | years |
|---|---|---|---|---|---|---|---|
| google/gemma-4-12b-qat | fast | 1/1 | 5/6 | 4/4 | 3/4 | 3/4 | 2/2 |
| openai/gpt-oss-20b | fast | 1/1 | 1/6 | 4/4 | 3/4 | 4/4 | 1/2 |
| openai/gpt-oss-20b | think | 1/1 | 1/6 | 4/4 | 3/4 | 4/4 | 1/2 |
| qwen/qwen3-30b-a3b | fast | 1/1 | 4/6 | 4/4 | 3/4 | 4/4 | 1/2 |
| qwen/qwen3-30b-a3b | think | 1/1 | 2/6 | 4/4 | 3/4 | 4/4 | 2/2 |

## google/gemma-4-12b-qat [fast]

finish reasons: ['stop', 'stop', 'stop', 'stop', 'stop', 'stop']

- **ashby_vultr_24c3445f-27b2-40fb-870e-e59b71305787** (17 items, 38.49 s)
  - probe "accepting applications from candidates residing in the following states": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'work_authorization', 'min_years': None}
- **greenhouse_axonius_8009643003** (10 items, 21.18 s)
  - probe not extracted: "Active-duty military service member approved to participate in the DoD SkillBridge program"
- **greenhouse_huntress_7743188003** (21 items, 42.09 s)
  - info "Location: Remote, US (West Coast)": [{'jd_quote': 'Location: Remote, US (West Coast)', 'importance': 'hard', 'eligibility_type': 'work_authorization', 'subject': 'candidate'}]
- **greenhouse_keepersecurity_4459433009** (25 items, 51.18 s)
  - probe "Must reside within reasonable commuting distance of Keeper": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'work_authorization', 'min_years': None}

Snippet misses:
  - neg_mixed_clearance (negation): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None} fields={'importance': False, 'polarity': True, 'eligibility_type': True}
  - subj_stack (subject): [{'text': 'Terraform', 'jd_quote': 'Terraform', 'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}, {'text': 'Ansible', 'jd_quote': 'Ansible', 'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}, {'text': 'Datadog', 'jd_quote': 'Datadog', 'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}] fields=None
  - elig_reside (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'work_authorization', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - info imp_conflict_aws: [{'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate'}]

## openai/gpt-oss-20b [fast]

finish reasons: ['stop', 'stop', 'stop', 'stop', 'stop', 'stop']

- **ashby_vultr_24c3445f-27b2-40fb-870e-e59b71305787** (15 items, 23.92 s)
  - quote not found in posting: "Hands-on experience configuring and supporting: Databases and caching solutions"
  - quote not found in posting: "Hands-on experience configuring and supporting: Firewalls"
  - quote not found in posting: "Demonstrated understanding of: Server and application architecture, including security har"
  - quote not found in posting: "Demonstrated understanding of: Distributed storage solutions (e.g., Ceph)"
  - quote not found in posting: "Demonstrated understanding of: Virtualization technologies"
  - quote not found in posting: "Demonstrated understanding of: Load balancing concepts"
  - quote not found in posting: "Demonstrated understanding of: Core networking concepts"
- **greenhouse_gitlab_8859063002** (10 items, 21.71 s)
  - probe "you must be a United States Citizen": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None}
  - probe not extracted: "overnight shift role with working hours of 12:00am - 7:00am EST"
- **greenhouse_huntress_7743188003** (20 items, 31.55 s)
  - info "Location: Remote, US (West Coast)": []
- **greenhouse_keepersecurity_4459433009** (25 items, 39.87 s)
  - probe "Must reside within reasonable commuting distance of Keeper": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'work_authorization', 'min_years': None}
  - probe "Ability to lift up to 50 pounds": wrong ['importance', 'eligibility_type']; got {'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}
- **greenhouse_tailscale_4710785005** (12 items, 21.24 s)
  - probe not extracted: "Prior Go expertise is not a requirement"

Snippet misses:
  - neg_mixed_clearance (negation): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None} fields={'importance': False, 'polarity': True, 'eligibility_type': True}
  - elig_citizen (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_reside (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_sponsor (eligibility): {'importance': 'hard', 'polarity': 'negated', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_lift (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_travel (eligibility): {'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - years_pref (years): {'importance': 'preferred', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'min_years': False}
  - info imp_conflict_aws: [{'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate'}]

## openai/gpt-oss-20b [think]

finish reasons: ['stop', 'stop', 'stop', 'stop', 'stop', 'stop']

- **ashby_vultr_24c3445f-27b2-40fb-870e-e59b71305787** (15 items, 23.66 s)
  - quote not found in posting: "Hands-on experience configuring and supporting: Databases and caching solutions"
  - quote not found in posting: "Hands-on experience configuring and supporting: Firewalls"
  - quote not found in posting: "Demonstrated understanding of: Server and application architecture, including security har"
  - quote not found in posting: "Demonstrated understanding of: Distributed storage solutions (e.g., Ceph)"
  - quote not found in posting: "Demonstrated understanding of: Virtualization technologies"
  - quote not found in posting: "Demonstrated understanding of: Load balancing concepts"
  - quote not found in posting: "Demonstrated understanding of: Core networking concepts"
- **greenhouse_gitlab_8859063002** (10 items, 21.97 s)
  - probe "you must be a United States Citizen": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None}
  - probe not extracted: "overnight shift role with working hours of 12:00am - 7:00am EST"
- **greenhouse_huntress_7743188003** (20 items, 31.41 s)
  - info "Location: Remote, US (West Coast)": []
- **greenhouse_keepersecurity_4459433009** (25 items, 39.06 s)
  - probe "Must reside within reasonable commuting distance of Keeper": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'work_authorization', 'min_years': None}
  - probe "Ability to lift up to 50 pounds": wrong ['importance', 'eligibility_type']; got {'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}
- **greenhouse_tailscale_4710785005** (12 items, 21.07 s)
  - probe not extracted: "Prior Go expertise is not a requirement"

Snippet misses:
  - neg_mixed_clearance (negation): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None} fields={'importance': False, 'polarity': True, 'eligibility_type': True}
  - elig_citizen (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_reside (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_sponsor (eligibility): {'importance': 'hard', 'polarity': 'negated', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_lift (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_travel (eligibility): {'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - years_pref (years): {'importance': 'preferred', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'min_years': False}
  - info imp_conflict_aws: [{'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate'}]

## qwen/qwen3-30b-a3b [fast]

finish reasons: ['stop', 'stop', 'stop', 'stop', 'stop', 'stop']

- **greenhouse_gitlab_8859063002** (10 items, 66.48 s)
  - probe not extracted: "overnight shift role with working hours of 12:00am - 7:00am EST"
- **greenhouse_huntress_7743188003** (34 items, 192.97 s)
  - info "Location: Remote, US (West Coast)": [{'jd_quote': 'Location: Remote, US (West Coast)', 'importance': 'hard', 'eligibility_type': 'physical', 'subject': 'candidate'}]
- **greenhouse_keepersecurity_4459433009** (25 items, 147.36 s)
  - probe "Must reside within reasonable commuting distance of Keeper": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}
  - probe "Ability to lift up to 50 pounds": wrong ['importance', 'eligibility_type']; got {'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}
- **greenhouse_tailscale_4710785005** (12 items, 51.6 s)
  - probe not extracted: "Prior Go expertise is not a requirement"

Snippet misses:
  - neg_mixed_clearance (negation): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None} fields={'importance': False, 'polarity': True, 'eligibility_type': True}
  - elig_lift (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_travel (eligibility): {'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - years_pref (years): {'importance': 'preferred', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'min_years': False}
  - info imp_conflict_aws: [{'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate'}]

## qwen/qwen3-30b-a3b [think]

finish reasons: ['stop', 'stop', 'stop', 'stop', 'stop', 'stop']

- **ashby_vultr_24c3445f-27b2-40fb-870e-e59b71305787** (15 items, 102.95 s)
  - probe "Familiarity with scripting languages such as Bash, Python": wrong ['importance']; got {'importance': 'preferred', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}
- **greenhouse_gitlab_8859063002** (10 items, 163.15 s)
  - probe "you must be a United States Citizen": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None}
  - probe "Familiarity with DevOps practices and container-orchestration": wrong ['importance']; got {'importance': 'preferred', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None}
  - probe not extracted: "overnight shift role with working hours of 12:00am - 7:00am EST"
- **greenhouse_huntress_7743188003** (21 items, 170.6 s)
  - info "Location: Remote, US (West Coast)": [{'jd_quote': 'Location: Remote, US (West Coast)', 'importance': 'hard', 'eligibility_type': 'physical', 'subject': 'candidate'}]
- **greenhouse_keepersecurity_4459433009** (25 items, 174.21 s)
  - probe "Must reside within reasonable commuting distance of Keeper": wrong ['eligibility_type']; got {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'work_authorization', 'min_years': None}
- **greenhouse_tailscale_4710785005** (13 items, 144.51 s)
  - probe not extracted: "Prior Go expertise is not a requirement"

Snippet misses:
  - neg_mixed_clearance (negation): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'clearance', 'min_years': None} fields={'importance': False, 'polarity': True, 'eligibility_type': True}
  - elig_citizen (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_reside (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_lift (eligibility): {'importance': 'hard', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - elig_travel (eligibility): {'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate', 'eligibility_type': 'none', 'min_years': None} fields={'importance': True, 'eligibility_type': False}
  - info imp_conflict_aws: [{'importance': 'required', 'polarity': 'affirmative', 'subject': 'candidate'}]
