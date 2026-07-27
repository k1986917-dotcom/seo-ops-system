"""
Content Scorer

Multi-dimensional content quality scoring system that evaluates:
- Humanity/Voice (30%): Human tone, personality, conversational devices
- Specificity (25%): Concrete examples vs vague generalizations
- Structure Balance (20%): Prose-to-list ratio (target 50-75%)
- SEO Compliance (15%): Keyword density, meta, structure
- Readability (10%): Flesch score, sentence rhythm, paragraph length

Readability now includes:
- Flesch Reading Ease score (target 60-70)
- Sentence rhythm scoring (penalizes monotonous sections)
- Paragraph length check (flags paragraphs >4 sentences)

Composite score must be >= 70 to pass quality threshold.
"""

import re
import sys
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

# Import existing modules
try:
    from .readability_scorer import ReadabilityScorer
    from .seo_quality_rater import SEOQualityRater
except ImportError:
    # For standalone testing
    from readability_scorer import ReadabilityScorer
    from seo_quality_rater import SEOQualityRater

# Centralized thresholds/weights (single source of truth).
try:
    from seo_config import CONTENT_WEIGHTS, PASS_SCORE
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    _sc_cfg = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_sc_cfg)
    CONTENT_WEIGHTS = _sc_cfg.CONTENT_WEIGHTS
    PASS_SCORE = _sc_cfg.PASS_SCORE


class ContentScorer:
    """Multi-dimensional content quality scorer"""

    # Dimension weights (must sum to 1.0).
    # v2 calibrated via backtest on 44 published laserpointerhub articles (2026-06-03).
    # seo raised from 0.15→0.25 (was artificially low for an SEO system; the real drag
    # was the parser not seeing frontmatter fields, fixed separately).
    # readability lowered 0.10→0.05 (technical laser content has intrinsically lower
    # Flesch scores; reducing weight prevents penalising articles for being technical).
    # humanity lowered 0.30→0.25 (less dependency on style, more on substance).
    # Backtest result: 43/44 pass unchanged, mean 78.7→79.2, max shift ±3.3.
    # Values now live in seo_config.CONTENT_WEIGHTS; kept here as a class attr alias
    # so callers using ContentScorer.WEIGHTS keep working.
    WEIGHTS = CONTENT_WEIGHTS

    # Threshold for passing (sourced from seo_config.PASS_SCORE)
    PASS_THRESHOLD = PASS_SCORE

    # AI phrases to detect (reduce humanity score)
    AI_PHRASES = [
        r'\bin today\'s (?:digital|modern|fast-paced)\b',
        r'\bwhen it comes to\b',
        r'\bit\'s important to (?:note|remember|understand)\b',
        r'\bin the world of\b',
        r'\blet\'s dive (?:in|into)\b',
        r'\bfurthermore\b',
        r'\bmoreover\b',
        r'\badditionally\b',
        r'\bin order to\b',
        r'\bdue to the fact that\b',
        r'\bat the end of the day\b',
        r'\bgoing forward\b',
        r'\bleverage\b',
        r'\butilize\b',
        r'\bsynergy\b',
        r'\bholistic\b',
        r'\brobust\b',
        r'\bseamless(?:ly)?\b',
        r'\bgame.?changer\b',
        r'\bunlock(?:ing)? (?:the )?(?:power|potential)\b',
        r'\btake (?:your|it) to the next level\b',
        r'\bjourney\b(?! to\b)',  # "journey" alone, not "journey to"
        r'\blandscape\b',
        r'\bparadigm\b',
        r'\boptimal\b',
        r'\bfacilitate\b',
    ]

    # Vague words that reduce specificity
    VAGUE_WORDS = [
        r'\bmany\b',
        r'\bsome\b',
        r'\bvarious\b',
        r'\bnumerous\b',
        r'\bseveral\b',
        r'\boften\b',
        r'\bsometimes\b',
        r'\busually\b',
        r'\bgenerally\b',
        r'\btypically\b',
        r'\bsignificant(?:ly)?\b',
        r'\bsubstantial(?:ly)?\b',
        r'\bconsiderable\b',
        r'\bgreat(?:ly)?\b',
        r'\bvery\b',
        r'\breally\b',
        r'\bquite\b',
        r'\brather\b',
        r'\brelatively\b',
        r'\brecently\b',
        r'\bcurrently\b',
        r'\beffective(?:ly)?\b',
        r'\bimportant\b',
        r'\bessential\b',
        r'\bcritical\b',
        r'\bkey\b',
        r'\bcrucial\b',
    ]

    # Specificity indicators (boost specificity score)
    SPECIFICITY_PATTERNS = [
        r'\b\d{1,3}%\b',  # Percentages
        r'\$[\d,]+(?:\.\d{2})?\b',  # Dollar amounts
        r'\b\d{4}\b',  # Years
        r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}',  # Dates
        r'\b\d+(?:,\d{3})*\s*(?:downloads?|listeners?|subscribers?|episodes?|users?|customers?)\b',  # Counts
        r'(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:said|says|explained|noted|mentioned)',  # Quotes with names
        r'\"[^\"]{10,}\"',  # Quoted text
    ]

    # Conversational devices (boost humanity score)
    CONVERSATIONAL_PATTERNS = [
        r'\([^)]{5,50}\)',  # Parenthetical asides
        r'\?(?:\s|$)',  # Questions
        r'\bdon\'t\b',  # Contractions
        r'\bcan\'t\b',
        r'\bwon\'t\b',
        r'\byou\'re\b',
        r'\byou\'ve\b',
        r'\bit\'s\b',
        r'\bthat\'s\b',
        r'\bhere\'s\b',
        r'\blet\'s\b',
        r'\bI\'ve\b',
        r'\bI\'m\b',
        r'\bwe\'ve\b',
        r'\bwe\'re\b',
        r'(?:^|\.\s+)(?:Look|Here\'s the thing|The truth is|Sound familiar|Trust me)',  # Casual openers
    ]

    # E-E-A-T Experience signals — first-person hands-on testing indicators.
    # Verb list widened: previously legitimate first-person like "we ran tests"
    # scored 0 because only tested/measured/found… were recognised.
    EEAT_EXPERIENCE_PATTERNS = [
        r'\bin our (?:hands-on )?(?:test|testing|evaluation|review|experience|trial|lab)\b',
        r'\bwe (?:tested|measured|found|discovered|observed|noticed|were surprised|ran|'
        r'conducted|compared|evaluated|benchmarked|examined|verified|confirmed|tried|put)\b',
        r'\b(?:our|we ran)\s+(?:bench|side-by-side|head-to-head|controlled)\b',
        r'\bafter (?:\d+ (?:days?|weeks?|months?) of|our)\b',
        r'\bduring our (?:field test|evaluation|hands-on|testing)\b',
    ]

    # E-E-A-T 深度校验 — 体验词前后必须有具体数字或对比结构才计为有效
    EEAT_SPECIFICITY_PATTERNS = {
        'numbers': r'\b\d+(?:\.\d+)?\s*(?:nm|w|mw|v|ma|mah|\$|%|meters|feet|inches|hours?|minutes?|seconds?)\b',
        'comparison': r'\b(?:compared\s+to|vs|versus|compared\s+with|brighter|longer|shorter|higher|lower|faster|slower|darker|quieter|louder)\b',
    }

    # Anchor text blacklist (generic/non-descriptive link text)
    ANCHOR_BLACKLIST = ['click here', 'read more', 'learn more', 'this page', 'here']

    def __init__(self):
        self.readability_scorer = ReadabilityScorer()
        self.seo_rater = SEOQualityRater()

    def score(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Score content across all dimensions

        Args:
            content: Full article content (markdown)
            metadata: Optional dict with meta_title, meta_description,
                     primary_keyword, secondary_keywords

        Returns:
            Dict with composite_score, passed, dimensions, and priority_fixes
        """
        metadata = metadata or {}

        # Clean content for analysis
        clean_content = self._clean_for_analysis(content)

        # Score each dimension
        humanity = self._score_humanity(clean_content)
        specificity = self._score_specificity(clean_content)
        structure = self._score_structure_balance(content)
        seo = self._score_seo(content, metadata)
        readability = self._score_readability(clean_content)

        # Calculate composite score
        composite = (
            humanity['score'] * self.WEIGHTS['humanity'] +
            specificity['score'] * self.WEIGHTS['specificity'] +
            structure['score'] * self.WEIGHTS['structure_balance'] +
            seo['score'] * self.WEIGHTS['seo'] +
            readability['score'] * self.WEIGHTS['readability']
        )
        composite = round(composite, 1)

        # Determine if passed
        passed = composite >= self.PASS_THRESHOLD

        # Collect all issues and prioritize
        all_issues = []
        for dim_name, dim_data in [
            ('humanity', humanity),
            ('specificity', specificity),
            ('structure_balance', structure),
            ('seo', seo),
            ('readability', readability)
        ]:
            for issue in dim_data.get('issues', []):
                issue['dimension'] = dim_name
                issue['dimension_score'] = dim_data['score']
                all_issues.append(issue)

        # Sort by impact (dimension weight * score deficit)
        for issue in all_issues:
            weight = self.WEIGHTS[issue['dimension']]
            deficit = 100 - issue['dimension_score']
            issue['impact'] = weight * deficit

        priority_fixes = sorted(all_issues, key=lambda x: -x['impact'])[:5]

        return {
            'composite_score': composite,
            'passed': passed,
            'threshold': self.PASS_THRESHOLD,
            'dimensions': {
                'humanity': {
                    'score': humanity['score'],
                    'weight': self.WEIGHTS['humanity'],
                    'issues': humanity.get('issues', []),
                    'details': humanity.get('details', {})
                },
                'specificity': {
                    'score': specificity['score'],
                    'weight': self.WEIGHTS['specificity'],
                    'issues': specificity.get('issues', []),
                    'details': specificity.get('details', {})
                },
                'structure_balance': {
                    'score': structure['score'],
                    'weight': self.WEIGHTS['structure_balance'],
                    'prose_ratio': structure.get('prose_ratio', 0),
                    'issues': structure.get('issues', []),
                    'details': structure.get('details', {})
                },
                'seo': {
                    'score': seo['score'],
                    'weight': self.WEIGHTS['seo'],
                    'issues': seo.get('issues', []),
                    'details': seo.get('details', {})
                },
                'readability': {
                    'score': readability['score'],
                    'weight': self.WEIGHTS['readability'],
                    'flesch': readability.get('flesch', 0),
                    'issues': readability.get('issues', []),
                    'details': readability.get('details', {})
                }
            },
            'priority_fixes': priority_fixes
        }

    def _clean_for_analysis(self, content: str) -> str:
        """Remove markdown formatting for text analysis"""
        text = content

        # Remove frontmatter/metadata block
        text = re.sub(r'^\*\*[^*]+\*\*:\s*.+$', '', text, flags=re.MULTILINE)

        # Remove horizontal rules
        text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)

        # Remove code blocks
        text = re.sub(r'```[^`]*```', '', text)

        # Remove links but keep text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

        # Remove bold/italic markers
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)

        # Remove headers but keep text
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)

        return text.strip()

    def _score_humanity(self, content: str) -> Dict[str, Any]:
        """Score content for human voice and personality"""
        issues = []
        details = {}

        content_lower = content.lower()
        word_count = len(content.split())

        # Count AI phrases
        ai_phrase_count = 0
        ai_phrases_found = []
        for pattern in self.AI_PHRASES:
            matches = re.findall(pattern, content_lower)
            ai_phrase_count += len(matches)
            if matches:
                ai_phrases_found.extend(matches[:2])  # Keep first 2 examples

        ai_density = (ai_phrase_count / max(word_count, 1)) * 1000  # per 1000 words
        details['ai_phrases_per_1000'] = round(ai_density, 1)
        details['ai_phrases_found'] = ai_phrases_found[:5]

        # Count conversational devices
        conversational_count = 0
        for pattern in self.CONVERSATIONAL_PATTERNS:
            conversational_count += len(re.findall(pattern, content, re.IGNORECASE))

        conv_density = (conversational_count / max(word_count, 1)) * 1000
        details['conversational_per_1000'] = round(conv_density, 1)

        # Count passive voice (simplified)
        passive_indicators = len(re.findall(r'\b(?:is|are|was|were|been|being)\s+\w+ed\b', content_lower))
        passive_ratio = passive_indicators / max(word_count / 100, 1)
        details['passive_voice_ratio'] = round(passive_ratio, 2)

        # Count contractions
        contractions = len(re.findall(r"'(?:t|s|re|ve|ll|d|m)\b", content))
        contraction_density = (contractions / max(word_count, 1)) * 100
        details['contractions_per_100'] = round(contraction_density, 1)

        # Calculate score
        score = 100

        # Penalize AI phrases (up to -30)
        if ai_density > 5:
            penalty = min(30, (ai_density - 5) * 3)
            score -= penalty
            issues.append({
                'issue': f'AI phrases detected ({ai_phrase_count} instances)',
                'fix': f'Remove or rephrase: {", ".join(ai_phrases_found[:3])}',
                'severity': 'high' if ai_density > 10 else 'medium'
            })

        # Penalize high passive voice (up to -15)
        if passive_ratio > 2:
            penalty = min(15, (passive_ratio - 2) * 5)
            score -= penalty
            issues.append({
                'issue': 'High passive voice usage',
                'fix': 'Convert passive sentences to active voice',
                'severity': 'medium'
            })

        # Reward conversational devices (up to +15)
        if conv_density > 3:
            bonus = min(15, (conv_density - 3) * 2)
            score = min(100, score + bonus)

        # Penalize lack of contractions (up to -10)
        if contraction_density < 1:
            score -= 10
            issues.append({
                'issue': 'Lacks contractions (sounds formal)',
                'fix': "Use contractions like don't, can't, you're, it's",
                'severity': 'low'
            })

        # E-E-A-T Experience signal detection
        eeat_count = 0
        for pattern in self.EEAT_EXPERIENCE_PATTERNS:
            eeat_count += len(re.findall(pattern, content, re.IGNORECASE))
        details['eeat_experience_count'] = eeat_count

        if eeat_count < 2:
            score -= 15
            issues.append({
                'issue': f'E-E-A-T Experience: Lacks first-person hands-on testing language (found {eeat_count}, need ≥2)',
                'fix': 'Add phrases like "In our hands-on test...", "We discovered...", "After 30 days of testing..."',
                'severity': 'high' if eeat_count == 0 else 'medium'
            })
        else:
            # E-E-A-T 深度校验 — 体验词前后是否有具体数字或对比支撑
            eeat_result = self._verify_eeat_specificity(content)
            eeat_valid = eeat_result.get('valid', 0)
            eeat_hollow = eeat_result.get('hollow', 0)
            details['eeat_valid_signals'] = eeat_valid
            details['eeat_hollow_signals'] = eeat_hollow

            if eeat_hollow > 0 and eeat_valid < eeat_hollow:
                penalty = min(15, eeat_hollow * 5)
                score -= penalty
                issues.append({
                    'issue': f'E-E-A-T Specificity: {eeat_hollow} hollow experience claims lack numbers or comparisons (only {eeat_valid}/{eeat_valid + eeat_hollow} verified)',
                    'fix': 'Add specific numbers (nm, mW, $, minutes) or comparisons (vs, compared to) near experience claims like "in our test" or "we tested"',
                    'severity': 'high' if eeat_hollow >= 3 else 'medium'
                })

        # Author Bio Block detection
        has_author_bio = bool(
            re.search(r'\[Author Bio Block', content, re.IGNORECASE) or
            re.search(r'About the Author', content, re.IGNORECASE) or
            re.search(r'\[Reviewed By', content, re.IGNORECASE)
        )
        details['has_author_bio_block'] = has_author_bio

        if not has_author_bio:
            score -= 10
            issues.append({
                'issue': 'E-E-A-T Expertise: Missing author bio block ([Author Bio Block] or "About the Author")',
                'fix': 'Add "[Author Bio Block]" with author name, credentials, and [Reviewed By: Expert Name] at end of article',
                'severity': 'medium'
            })

        # Linkable Asset placeholder detection (suggestion only, not mandatory)
        has_linkable_asset = bool(
            re.search(r'\[Insert Interactive', content, re.IGNORECASE) or
            re.search(r'\[Insert Downloadable', content, re.IGNORECASE)
        )
        details['has_linkable_asset'] = has_linkable_asset

        if not has_linkable_asset:
            score -= 5
            issues.append({
                'issue': 'E-E-A-T Authoritativeness: No linkable asset placeholder found',
                'fix': 'If material pack G has a calculator/checklist, insert [Insert Interactive Calculator Placeholder] or [Insert Downloadable PDF Checklist Link] in the article',
                'severity': 'low'
            })

        return {
            'score': max(0, min(100, round(score))),
            'issues': issues,
            'details': details
        }

    def _verify_eeat_specificity(self, content: str) -> Dict[str, int]:
        """
        深度校验 E-E-A-T 的真实性与具体度。
        检测到 "in our test" / "we tested" 等体验词后，
        检查其前后 120 字符内是否包含具体参数数字或对比结构。
        防止 AI 编造空洞的体验描述（Hollow Claims）。
        """
        eeat_pattern = r'(?:in our (?:hands-on )?(?:test|testing|evaluation|review|experience|trial|lab)|we (?:tested|measured|found|discovered|observed|ran|conducted|compared|evaluated|benchmarked|examined|verified|confirmed|tried|put)|during our (?:field test|evaluation|hands-on|testing)|after \d+ (?:days?|weeks?|months?) of)'
        matches = list(re.finditer(eeat_pattern, content, re.IGNORECASE))

        if not matches:
            return {'valid': 0, 'hollow': 0}

        valid = 0
        hollow = 0
        for m in matches:
            start = max(0, m.start() - 50)
            end = min(len(content), m.end() + 120)
            context = content[start:end]

            has_numbers = bool(re.search(
                self.EEAT_SPECIFICITY_PATTERNS['numbers'], context, re.IGNORECASE
            ))
            has_comparison = bool(re.search(
                self.EEAT_SPECIFICITY_PATTERNS['comparison'], context, re.IGNORECASE
            ))

            if has_numbers or has_comparison:
                valid += 1
            else:
                hollow += 1

        return {'valid': valid, 'hollow': hollow}

    def _score_specificity(self, content: str) -> Dict[str, Any]:
        """Score content for concrete examples vs vague generalizations"""
        issues = []
        details = {}

        content_lower = content.lower()
        word_count = len(content.split())

        # Count vague words
        vague_count = 0
        vague_found = []
        for pattern in self.VAGUE_WORDS:
            matches = re.findall(pattern, content_lower)
            vague_count += len(matches)
            if matches and len(vague_found) < 5:
                vague_found.extend(matches[:2])

        vague_density = (vague_count / max(word_count, 1)) * 1000
        details['vague_words_per_1000'] = round(vague_density, 1)
        details['vague_words_found'] = list(set(vague_found))[:5]

        # Count specificity indicators
        specific_count = 0
        for pattern in self.SPECIFICITY_PATTERNS:
            specific_count += len(re.findall(pattern, content))

        specific_density = (specific_count / max(word_count, 1)) * 1000
        details['specifics_per_1000'] = round(specific_density, 1)

        # Count numbers and data points
        numbers = re.findall(r'\b\d+(?:,\d{3})*(?:\.\d+)?\b', content)
        number_density = (len(numbers) / max(word_count, 1)) * 1000
        details['numbers_per_1000'] = round(number_density, 1)

        # Calculate score
        score = 70  # Start at baseline

        # Penalize vague words (up to -25)
        if vague_density > 15:
            penalty = min(25, (vague_density - 15) * 1.5)
            score -= penalty
            issues.append({
                'issue': f'Too many vague words ({vague_count} instances)',
                'fix': f'Replace vague words with specifics: {", ".join(vague_found[:3])}',
                'severity': 'high' if vague_density > 25 else 'medium'
            })

        # Reward specificity indicators (up to +30)
        if specific_density > 2:
            bonus = min(30, specific_density * 5)
            score += bonus

        # Penalize lack of numbers/data (up to -15)
        if number_density < 3:
            penalty = min(15, (3 - number_density) * 5)
            score -= penalty
            issues.append({
                'issue': 'Lacks specific numbers and data',
                'fix': 'Add percentages, dollar amounts, dates, or counts',
                'severity': 'medium'
            })

        return {
            'score': max(0, min(100, round(score))),
            'issues': issues,
            'details': details
        }

    def _score_structure_balance(self, content: str) -> Dict[str, Any]:
        """Score content for prose-to-structure ratio"""
        issues = []
        details = {}

        # Remove metadata block
        content = re.sub(r'^\*\*[^*]+\*\*:\s*.+$', '', content, flags=re.MULTILINE)
        content = re.sub(r'^---+\s*$', '', content, flags=re.MULTILINE)

        lines = content.split('\n')

        list_chars = 0
        table_chars = 0
        header_chars = 0
        total_chars = 0

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            char_count = len(line_stripped)
            total_chars += char_count

            # List items
            if re.match(r'^[-*+]\s', line_stripped) or re.match(r'^\d+\.\s', line_stripped):
                list_chars += char_count
            # Table rows
            elif '|' in line_stripped:
                table_chars += char_count
            # Headers
            elif re.match(r'^#+\s', line_stripped):
                header_chars += char_count

        structured_chars = list_chars + table_chars
        prose_chars = total_chars - structured_chars - header_chars

        prose_ratio = prose_chars / max(total_chars - header_chars, 1)
        list_ratio = list_chars / max(total_chars, 1)
        table_ratio = table_chars / max(total_chars, 1)

        details['prose_ratio'] = round(prose_ratio, 2)
        details['list_ratio'] = round(list_ratio, 2)
        details['table_ratio'] = round(table_ratio, 2)
        details['prose_chars'] = prose_chars
        details['list_chars'] = list_chars
        details['table_chars'] = table_chars

        # Calculate score
        # Target: 50-75% prose (sweet spot for readable, scannable content)
        if 0.50 <= prose_ratio <= 0.75:
            score = 100
        elif prose_ratio < 0.50:
            # Too structured - needs more narrative
            deficit = 0.50 - prose_ratio
            score = max(0, 100 - (deficit * 150))
            issues.append({
                'issue': f'Too much structure ({round(prose_ratio * 100)}% prose, target 50-75%)',
                'fix': 'Convert some bullet lists or tables to prose paragraphs',
                'severity': 'high' if prose_ratio < 0.35 else 'medium'
            })
        else:
            # Too prose-heavy - needs more structure for scannability
            excess = prose_ratio - 0.75
            score = max(0, 100 - (excess * 100))
            issues.append({
                'issue': f'Too much prose ({round(prose_ratio * 100)}% prose, target 50-75%)',
                'fix': 'Add tables for comparisons, lists for features, or visual breaks',
                'severity': 'medium' if prose_ratio < 0.90 else 'high'
            })

        return {
            'score': round(score),
            'prose_ratio': round(prose_ratio, 2),
            'issues': issues,
            'details': details
        }

    def _score_seo(self, content: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Score content for SEO compliance"""
        issues = []
        details = {}

        meta_title = metadata.get('meta_title', '')
        meta_description = metadata.get('meta_description', '')
        primary_keyword = metadata.get('primary_keyword', '')

        # Extract from content if not provided
        if not meta_title:
            match = re.search(r'\*\*Meta Title\*\*:\s*(.+)', content)
            if match:
                meta_title = match.group(1).strip()

        if not meta_description:
            match = re.search(r'\*\*Meta Description\*\*:\s*(.+)', content)
            if match:
                meta_description = match.group(1).strip()

        if not primary_keyword:
            match = re.search(r'\*\*(?:Target|Primary) Keyword\*\*:\s*(.+)', content)
            if match:
                primary_keyword = match.group(1).strip()

        details['meta_title'] = meta_title
        details['meta_title_length'] = len(meta_title)
        details['meta_description'] = meta_description[:100] + '...' if len(meta_description) > 100 else meta_description
        details['meta_description_length'] = len(meta_description)
        details['primary_keyword'] = primary_keyword

        score = 100

        # Check meta title length (50-60)
        if not meta_title:
            score -= 15
            issues.append({
                'issue': 'Missing meta title',
                'fix': 'Add a meta title (50-60 characters)',
                'severity': 'high'
            })
        elif len(meta_title) < 50:
            score -= 5
            issues.append({
                'issue': f'Meta title too short ({len(meta_title)} chars)',
                'fix': 'Expand meta title to 50-60 characters',
                'severity': 'low'
            })
        elif len(meta_title) > 60:
            score -= 5
            issues.append({
                'issue': f'Meta title too long ({len(meta_title)} chars)',
                'fix': 'Shorten meta title to 50-60 characters',
                'severity': 'low'
            })

        # Check meta description length (150-160)
        if not meta_description:
            score -= 15
            issues.append({
                'issue': 'Missing meta description',
                'fix': 'Add a meta description (150-160 characters)',
                'severity': 'high'
            })
        elif len(meta_description) < 150:
            score -= 5
        elif len(meta_description) > 160:
            score -= 5

        # Check keyword in H1
        h1_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if h1_match:
            h1 = h1_match.group(1).lower()
            details['h1'] = h1_match.group(1)
            if primary_keyword and primary_keyword.lower() not in h1:
                score -= 10
                issues.append({
                    'issue': 'Primary keyword not in H1',
                    'fix': f'Include "{primary_keyword}" in the H1 headline',
                    'severity': 'medium'
                })
        else:
            score -= 10
            issues.append({
                'issue': 'Missing H1 headline',
                'fix': 'Add an H1 headline with primary keyword',
                'severity': 'high'
            })

        # Check keyword in first 100 words
        clean_content = self._clean_for_analysis(content)
        first_100 = ' '.join(clean_content.split()[:100]).lower()
        if primary_keyword and primary_keyword.lower() not in first_100:
            score -= 10
            issues.append({
                'issue': 'Primary keyword not in first 100 words',
                'fix': f'Include "{primary_keyword}" in the introduction',
                'severity': 'medium'
            })

        # Word count
        word_count = len(clean_content.split())
        details['word_count'] = word_count
        if word_count < 2000:
            score -= 15
            issues.append({
                'issue': f'Content too short ({word_count} words)',
                'fix': 'Expand to at least 2,000 words',
                'severity': 'high'
            })

        # Anchor text safety check (descriptive anchor text is an SEO best practice)
        anchor_blacklist_count = 0
        blacklisted_anchors_found = []
        link_pattern = r'\[([^\]]+)\]\([^)]+\)'
        for match in re.finditer(link_pattern, content):
            anchor_text = match.group(1).strip().lower()
            if any(black in anchor_text for black in self.ANCHOR_BLACKLIST):
                anchor_blacklist_count += 1
                blacklisted_anchors_found.append(anchor_text)

        details['generic_anchor_count'] = anchor_blacklist_count

        if anchor_blacklist_count > 0:
            penalty = min(20, anchor_blacklist_count * 5)
            score -= penalty
            unique_bad = list(set(blacklisted_anchors_found))
            issues.append({
                'issue': f'Generic anchor text found ({anchor_blacklist_count}): {", ".join(unique_bad)}',
                'fix': 'Replace "click here" / "read more" / "learn more" with descriptive keyword anchor text',
                'severity': 'medium' if anchor_blacklist_count >= 3 else 'low'
            })

        return {
            'score': max(0, min(100, round(score))),
            'issues': issues,
            'details': details
        }

    def _score_readability(self, content: str) -> Dict[str, Any]:
        """Score content for readability, rhythm, and paragraph length"""
        issues = []
        details = {}

        # Use existing readability scorer
        try:
            analysis = self.readability_scorer.analyze(content)
            flesch = analysis.get('readability_metrics', {}).get('flesch_reading_ease', 50)
            grade = analysis.get('reading_level', 12)
        except Exception:
            # Fallback if module fails
            flesch = 60
            grade = 10

        details['flesch_reading_ease'] = flesch
        details['grade_level'] = grade

        # Target: Flesch 60-70 (fairly easy), Grade 8-10
        score = 100

        if flesch < 50:
            penalty = min(30, (50 - flesch) * 1.5)
            score -= penalty
            issues.append({
                'issue': f'Content too difficult (Flesch: {flesch})',
                'fix': 'Simplify sentences, use shorter words',
                'severity': 'high' if flesch < 40 else 'medium'
            })
        elif flesch < 60:
            score -= 10
            issues.append({
                'issue': f'Content slightly difficult (Flesch: {flesch})',
                'fix': 'Simplify some complex sentences',
                'severity': 'low'
            })
        elif flesch > 80:
            # Too simple (not heavily penalized)
            score -= 5

        if grade > 12:
            score -= 10
            issues.append({
                'issue': f'Reading level too high (Grade {grade})',
                'fix': 'Target 8th-10th grade reading level',
                'severity': 'medium'
            })

        # NEW: Check paragraph length (max 4 sentences)
        paragraph_issues = self._check_paragraph_length(content)
        details['long_paragraphs'] = paragraph_issues['count']
        details['longest_paragraph_sentences'] = paragraph_issues['longest']

        if paragraph_issues['count'] > 0:
            penalty = min(15, paragraph_issues['count'] * 3)
            score -= penalty
            issues.append({
                'issue': f'{paragraph_issues["count"]} paragraphs exceed 4 sentences (longest: {paragraph_issues["longest"]})',
                'fix': 'Break long paragraphs into smaller chunks (2-4 sentences max)',
                'severity': 'medium' if paragraph_issues['count'] < 5 else 'high'
            })

        # NEW: Check sentence rhythm (variety in length)
        rhythm_issues = self._check_sentence_rhythm(content)
        details['rhythm_score'] = rhythm_issues['rhythm_score']
        details['monotonous_sections'] = rhythm_issues['monotonous_count']

        if rhythm_issues['rhythm_score'] < 60:
            penalty = min(10, (60 - rhythm_issues['rhythm_score']) / 4)
            score -= penalty
            issues.append({
                'issue': f'Monotonous sentence rhythm ({rhythm_issues["monotonous_count"]} uniform sections)',
                'fix': 'Vary sentence length: mix short punchy (5-10 words) with longer flowing (15-25 words)',
                'severity': 'medium' if rhythm_issues['rhythm_score'] > 40 else 'high'
            })

        return {
            'score': max(0, min(100, round(score))),
            'flesch': flesch,
            'issues': issues,
            'details': details
        }

    def _check_paragraph_length(self, content: str) -> Dict[str, Any]:
        """Check for paragraphs that exceed 4 sentences"""
        # Split into paragraphs (double newline or blank line)
        paragraphs = re.split(r'\n\s*\n', content)

        long_paragraphs = 0
        longest = 0

        for para in paragraphs:
            para = para.strip()
            # Skip headers, lists, tables, metadata
            if not para or para.startswith('#') or para.startswith('-') or para.startswith('*') or para.startswith('|') or para.startswith('**Meta'):
                continue

            # Count sentences (rough: split by . ! ? followed by space or end)
            sentences = re.split(r'[.!?]+(?:\s|$)', para)
            sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

            sentence_count = len(sentences)
            if sentence_count > 4:
                long_paragraphs += 1
                longest = max(longest, sentence_count)

        return {
            'count': long_paragraphs,
            'longest': longest
        }

    def _check_sentence_rhythm(self, content: str) -> Dict[str, Any]:
        """Check for sentence length variety (rhythm)"""
        # Extract sentences
        sentences = re.split(r'[.!?]+(?:\s|$)', content)
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]

        if len(sentences) < 10:
            return {'rhythm_score': 70, 'monotonous_count': 0}  # Not enough to evaluate

        # Calculate word counts for each sentence
        word_counts = [len(s.split()) for s in sentences]

        # Check rhythm in sliding windows of 5 sentences
        monotonous_sections = 0
        window_size = 5

        for i in range(len(word_counts) - window_size + 1):
            window = word_counts[i:i + window_size]
            avg = sum(window) / len(window)

            # Check if all sentences in window are within 5 words of average (monotonous)
            all_similar = all(abs(wc - avg) <= 5 for wc in window)
            if all_similar:
                monotonous_sections += 1

        # Calculate rhythm score
        # More variety = higher score
        if len(word_counts) > 0:
            std_dev = (sum((wc - sum(word_counts)/len(word_counts))**2 for wc in word_counts) / len(word_counts)) ** 0.5
        else:
            std_dev = 0

        # Target std_dev around 8-15 (good variety)
        if std_dev < 5:
            rhythm_score = 40 + (std_dev * 6)  # Too uniform
        elif std_dev <= 15:
            rhythm_score = 100 - abs(10 - std_dev) * 2  # Sweet spot
        else:
            rhythm_score = 80  # High variety is fine

        # Penalize for monotonous sections
        rhythm_score -= monotonous_sections * 3
        rhythm_score = max(0, min(100, rhythm_score))

        return {
            'rhythm_score': round(rhythm_score),
            'monotonous_count': monotonous_sections,
            'std_dev': round(std_dev, 1)
        }

    def format_report(self, result: Dict[str, Any]) -> str:
        """Format scoring result as readable report"""
        lines = []
        lines.append("=" * 50)
        lines.append("CONTENT QUALITY SCORE")
        lines.append("=" * 50)
        lines.append("")

        status = "PASSED" if result['passed'] else "BELOW THRESHOLD"
        lines.append(f"Composite Score: {result['composite_score']}/100 ({status})")
        lines.append(f"Threshold: {result['threshold']}")
        lines.append("")

        lines.append("Dimensions:")
        for dim_name, dim_data in result['dimensions'].items():
            score = dim_data['score']
            weight = dim_data['weight']
            check = "OK" if score >= 70 else "NEEDS WORK"
            extra = ""
            if dim_name == 'structure_balance':
                extra = f" ({round(dim_data.get('prose_ratio', 0) * 100)}% prose)"
            elif dim_name == 'readability':
                flesch = dim_data.get('flesch', 0)
                details = dim_data.get('details', {})
                rhythm = details.get('rhythm_score', 'N/A')
                long_paras = details.get('long_paragraphs', 0)
                extra = f" (Flesch: {flesch}, Rhythm: {rhythm}, Long¶: {long_paras})"

            lines.append(f"  {dim_name:20} {score:3}/100 (weight: {int(weight*100)}%) [{check}]{extra}")

        lines.append("")

        if result['priority_fixes']:
            lines.append("Priority Fixes:")
            for i, fix in enumerate(result['priority_fixes'][:5], 1):
                dim = fix.get('dimension', 'unknown')
                issue = fix.get('issue', 'Unknown issue')
                suggestion = fix.get('fix', 'No suggestion')
                lines.append(f"  {i}. [{dim}] {issue}")
                lines.append(f"     Fix: {suggestion}")

        lines.append("")
        lines.append("=" * 50)

        return "\n".join(lines)


def main():
    """CLI entry point.

    Default output is the human-readable report (back-compat). Pass --json to get
    the raw result dict as JSON; write_collector uses this to read composite_score
    without parsing report text.
    """
    import argparse
    import json

    ap = argparse.ArgumentParser(description='Multi-dimensional content quality scorer')
    ap.add_argument('draft', help='Path to draft .md file to score')
    ap.add_argument('--json', dest='as_json', action='store_true',
                    help='Emit the result dict as JSON instead of the text report')
    args = ap.parse_args()

    file_path = args.draft
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    # Parse YAML frontmatter → metadata so the SEO dimension can see the meta
    # fields. Without this, every WRITE-format article (which stores SEO Title/
    # Description/Keywords in frontmatter) reported "Missing meta title" and lost
    # ~8 composite points. Uses the same colon-split as write_collector.
    metadata = {}
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            fm = {}
            for line in parts[1].strip().split('\n'):
                m = re.match(r'^([^:]+?)\s*:\s*(.+)', line.strip())
                if m:
                    fm[m.group(1).strip().lower()] = m.group(2).strip()
            metadata = {
                'meta_title': fm.get('seo title', '') or fm.get('title', ''),
                'meta_description': fm.get('seo description', '') or fm.get('summary', ''),
                'primary_keyword': (fm.get('seo keywords', '').split(',')[0].strip()
                                    if fm.get('seo keywords') else ''),
            }

    scorer = ContentScorer()
    result = scorer.score(content, metadata=metadata)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(scorer.format_report(result))


if __name__ == '__main__':
    main()
