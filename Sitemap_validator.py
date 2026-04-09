#!/usr/bin/env python3
"""
Sitemap URL Comparison and Validation Tool
Compares all URLs from baseline sitemap with DEV environment
Checks HTTP status codes and generates detailed HTML report
"""

import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urljoin
from datetime import datetime
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import os
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from colorama import Fore, Back, Style, init

# Initialize colorama
init(autoreset=True)

# Configuration
BASELINE_SITEMAP_URL = "https://vehiclehistory.eu/sitemap_index.xml"
DEV_BASE_URL = "https://vhreu.accessautohistory.com/"

# Output configuration
OUTPUT_DIR = "sitemap_comparison_results"
REPORT_FILE = f"{OUTPUT_DIR}/sitemap_comparison_report.html"
JSON_FILE = f"{OUTPUT_DIR}/sitemap_comparison_data.json"
LOG_FILE = f"{OUTPUT_DIR}/sitemap_comparison_log.txt"

# Create output directory
os.makedirs(OUTPUT_DIR, exist_ok=True)

@dataclass
class URLResult:
    """Stores result for a single URL comparison"""
    baseline_url: str
    dev_url: str
    path: str
    baseline_status: int = 0
    dev_status: int = 0
    status: str = "Pending"  # Passed, Failed, Error, Skipped
    response_time: float = 0.0
    error_message: str = ""
    content_type: str = ""
    page_title: str = ""
    redirect_url: str = ""
    exists_in_dev: bool = False
    
@dataclass
class SitemapStats:
    """Statistics for the comparison"""
    total_urls: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    total_time: float = 0.0
    average_response_time: float = 0.0
    
    # Additional stats
    status_200: int = 0
    status_301: int = 0
    status_302: int = 0
    status_404: int = 0
    status_500: int = 0
    other_status: int = 0

class SitemapParser:
    """Parses sitemap XML and extracts all URLs"""
    
    def __init__(self, sitemap_url: str):
        self.sitemap_url = sitemap_url
        self.urls = []
        self.sitemap_urls = []
        self.namespaces = {
            'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9',
            'image': 'http://www.google.com/schemas/sitemap-image/1.1',
            'news': 'http://www.google.com/schemas/sitemap-news/0.9',
            'video': 'http://www.google.com/schemas/sitemap-video/1.1',
            'mobile': 'http://www.google.com/schemas/sitemap-mobile/1.0'
        }
    
    def parse_sitemap(self) -> List[str]:
        """Parse the main sitemap index and extract all URLs"""
        print(f"\n{Fore.CYAN}📖 Parsing sitemap: {self.sitemap_url}")
        
        try:
            response = requests.get(self.sitemap_url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            response.raise_for_status()
            
            # Parse XML
            root = ET.fromstring(response.content)
            
            # Check if it's a sitemap index
            is_sitemap_index = root.tag.endswith('sitemapindex')
            
            if is_sitemap_index:
                print(f"{Fore.YELLOW}📁 Found sitemap index, extracting individual sitemaps...")
                self._parse_sitemap_index(root)
            else:
                print(f"{Fore.YELLOW}📄 Found single sitemap, extracting URLs...")
                self._parse_urlset(root)
            
            print(f"{Fore.GREEN}✅ Extracted {len(self.urls)} URLs from sitemap(s)")
            return self.urls
            
        except requests.RequestException as e:
            print(f"{Fore.RED}❌ Failed to fetch sitemap: {e}")
            return []
        except ET.ParseError as e:
            print(f"{Fore.RED}❌ Failed to parse XML: {e}")
            return []
    
    def _parse_sitemap_index(self, root):
        """Parse sitemap index and recursively parse each sitemap"""
        for sitemap in root.findall('.//sm:sitemap', self.namespaces):
            loc = sitemap.find('sm:loc', self.namespaces)
            if loc is not None and loc.text:
                self.sitemap_urls.append(loc.text)
        
        # Parse each sitemap
        for i, sitemap_url in enumerate(self.sitemap_urls, 1):
            print(f"{Fore.CYAN}  📄 Parsing sitemap {i}/{len(self.sitemap_urls)}: {sitemap_url}")
            try:
                response = requests.get(sitemap_url, timeout=30, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                response.raise_for_status()
                sitemap_root = ET.fromstring(response.content)
                self._parse_urlset(sitemap_root)
            except Exception as e:
                print(f"{Fore.RED}    ❌ Failed to parse sitemap: {e}")
    
    def _parse_urlset(self, root):
        """Parse URL set and extract all URLs"""
        # Try with namespace
        for url in root.findall('.//sm:url', self.namespaces):
            loc = url.find('sm:loc', self.namespaces)
            if loc is not None and loc.text:
                self.urls.append(loc.text)
        
        # If no URLs found with namespace, try without
        if len(self.urls) == 0:
            for url in root.findall('.//url'):
                loc = url.find('loc')
                if loc is not None and loc.text:
                    self.urls.append(loc.text)

class URLComparator:
    """Compares URLs between baseline and dev environments"""
    
    def __init__(self, baseline_url: str, dev_base_url: str):
        self.baseline_url = baseline_url
        self.dev_base_url = dev_base_url
        self.baseline_domain = urlparse(baseline_url).netloc
        self.results: List[URLResult] = []
        self.stats = SitemapStats()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # Parse baseline domain for path extraction
        self.baseline_parsed = urlparse(baseline_url)
        self.baseline_base = f"{self.baseline_parsed.scheme}://{self.baseline_parsed.netloc}"
        
        # Dev URL parsing
        self.dev_parsed = urlparse(dev_base_url)
        self.dev_base = dev_base_url.rstrip('/')
    
    def extract_path(self, url: str) -> str:
        """Extract path from URL, removing domain"""
        parsed = urlparse(url)
        path = parsed.path
        if parsed.query:
            path += f"?{parsed.query}"
        if parsed.fragment:
            path += f"#{parsed.fragment}"
        return path
    
    def create_dev_url(self, baseline_url: str) -> str:
        """Create DEV URL by replacing baseline domain with DEV domain"""
        path = self.extract_path(baseline_url)
        dev_url = urljoin(self.dev_base, path.lstrip('/'))
        return dev_url
    
    def check_url_status(self, url: str, timeout: int = 10) -> Tuple[int, float, str, str, str]:
        """Check URL status code and return details"""
        try:
            start_time = time.time()
            response = self.session.get(url, timeout=timeout, allow_redirects=True)
            response_time = time.time() - start_time
            
            status_code = response.status_code
            content_type = response.headers.get('content-type', '')
            
            # Extract page title
            page_title = ""
            if 'text/html' in content_type:
                import re
                title_match = re.search(r'<title>(.*?)</title>', response.text, re.IGNORECASE)
                if title_match:
                    page_title = title_match.group(1)[:100]
            
            # Check for redirect
            redirect_url = ""
            if response.history:
                redirect_url = response.url
            
            return status_code, response_time, content_type, page_title, redirect_url
            
        except requests.Timeout:
            return 0, timeout, "", "", "Timeout"
        except requests.ConnectionError:
            return 0, 0, "", "", "Connection Error"
        except Exception as e:
            return 0, 0, "", "", str(e)[:100]
    
    def compare_url(self, baseline_url: str) -> URLResult:
        """Compare a single URL between baseline and dev"""
        path = self.extract_path(baseline_url)
        dev_url = self.create_dev_url(baseline_url)
        
        result = URLResult(
            baseline_url=baseline_url,
            dev_url=dev_url,
            path=path
        )
        
        # Check baseline URL
        baseline_status, baseline_time, _, _, _ = self.check_url_status(baseline_url)
        result.baseline_status = baseline_status
        
        # Check DEV URL
        dev_status, dev_time, content_type, page_title, redirect_url = self.check_url_status(dev_url)
        result.dev_status = dev_status
        result.response_time = dev_time
        result.content_type = content_type
        result.page_title = page_title
        result.redirect_url = redirect_url
        
        # Determine status
        if dev_status == 200:
            result.status = "Passed"
            result.exists_in_dev = True
        elif dev_status == 404:
            result.status = "Failed"
            result.exists_in_dev = False
            result.error_message = "Page not found (404)"
        elif dev_status in [301, 302]:
            result.status = "Passed"  # Redirect is acceptable
            result.exists_in_dev = True
            result.error_message = f"Redirects to: {redirect_url}"
        elif dev_status >= 500:
            result.status = "Error"
            result.exists_in_dev = False
            result.error_message = f"Server error ({dev_status})"
        elif dev_status == 0:
            result.status = "Error"
            result.exists_in_dev = False
            result.error_message = f"Connection failed: {redirect_url}"
        else:
            result.status = "Error"
            result.exists_in_dev = False
            result.error_message = f"Unexpected status: {dev_status}"
        
        return result
    
    def compare_all_urls(self, urls: List[str], max_workers: int = 10) -> List[URLResult]:
        """Compare all URLs using thread pool"""
        print(f"\n{Fore.CYAN}🔍 Comparing {len(urls)} URLs with {max_workers} concurrent workers...")
        print(f"{Fore.CYAN}{'='*60}")
        
        self.stats.total_urls = len(urls)
        completed = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {executor.submit(self.compare_url, url): url for url in urls}
            
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                completed += 1
                
                try:
                    result = future.result()
                    self.results.append(result)
                    
                    # Update stats
                    if result.status == "Passed":
                        self.stats.passed += 1
                    elif result.status == "Failed":
                        self.stats.failed += 1
                    else:
                        self.stats.errors += 1
                    
                    # Update status code stats
                    if result.dev_status == 200:
                        self.stats.status_200 += 1
                    elif result.dev_status == 301:
                        self.stats.status_301 += 1
                    elif result.dev_status == 302:
                        self.stats.status_302 += 1
                    elif result.dev_status == 404:
                        self.stats.status_404 += 1
                    elif result.dev_status >= 500:
                        self.stats.status_500 += 1
                    elif result.dev_status > 0:
                        self.stats.other_status += 1
                    
                    self.stats.total_time += result.response_time
                    
                    # Progress indicator
                    status_color = Fore.GREEN if result.status == "Passed" else Fore.RED if result.status == "Failed" else Fore.YELLOW
                    status_icon = "✅" if result.status == "Passed" else "❌" if result.status == "Failed" else "⚠️"
                    
                    print(f"{status_color}[{completed:4d}/{len(urls)}] {status_icon} {result.path[:60]}... (Status: {result.dev_status})")
                    
                except Exception as e:
                    print(f"{Fore.RED}[{completed:4d}/{len(urls)}] ❌ Error comparing {url}: {e}")
                    self.stats.errors += 1
        
        if self.stats.total_urls > 0:
            self.stats.average_response_time = self.stats.total_time / self.stats.total_urls
        
        print(f"\n{Fore.GREEN}{'='*60}")
        print(f"{Fore.GREEN}✅ Comparison complete!")
        
        return self.results

class HTMLReportGenerator:
    """Generates HTML report from comparison results"""
    
    def __init__(self, results: List[URLResult], stats: SitemapStats, 
                 baseline_url: str, dev_url: str):
        self.results = results
        self.stats = stats
        self.baseline_url = baseline_url
        self.dev_url = dev_url
    
    def generate(self, output_file: str):
        """Generate HTML report"""
        
        # Calculate percentages
        pass_rate = (self.stats.passed / self.stats.total_urls * 100) if self.stats.total_urls > 0 else 0
        fail_rate = (self.stats.failed / self.stats.total_urls * 100) if self.stats.total_urls > 0 else 0
        error_rate = (self.stats.errors / self.stats.total_urls * 100) if self.stats.total_urls > 0 else 0
        
        # Generate table rows
        table_rows = ""
        for result in self.results:
            status_class = "passed" if result.status == "Passed" else "failed" if result.status == "Failed" else "error"
            status_icon = "✅" if result.status == "Passed" else "❌" if result.status == "Failed" else "⚠️"
            
            # Truncate long paths
            display_path = result.path[:80] + "..." if len(result.path) > 80 else result.path
            
            # Status badge
            status_badge = ""
            if result.dev_status == 200:
                status_badge = '<span class="badge badge-200">200 OK</span>'
            elif result.dev_status == 404:
                status_badge = '<span class="badge badge-404">404 Not Found</span>'
            elif result.dev_status in [301, 302]:
                status_badge = f'<span class="badge badge-redirect">{result.dev_status} Redirect</span>'
            elif result.dev_status >= 500:
                status_badge = f'<span class="badge badge-500">{result.dev_status} Error</span>'
            else:
                status_badge = f'<span class="badge badge-other">{result.dev_status}</span>'
            
            # Error message
            error_cell = result.error_message if result.error_message else "-"
            if result.redirect_url and result.dev_status in [301, 302]:
                error_cell = f"→ {result.redirect_url[:50]}..."
            
            table_rows += f"""
            <tr class="{status_class}">
                <td><input type="checkbox" class="row-checkbox"></td>
                <td>{status_icon}</td>
                <td class="path-cell" title="{result.path}">{display_path}</td>
                <td>{status_badge}</td>
                <td>{result.response_time:.2f}s</td>
                <td class="error-cell">{error_cell}</td>
                <td>
                    <a href="{result.baseline_url}" target="_blank" class="btn-link">Baseline</a>
                    <a href="{result.dev_url}" target="_blank" class="btn-link">DEV</a>
                </td>
            </tr>
            """
        
        # Generate summary cards
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sitemap Comparison Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #f5f7fa;
            color: #2c3e50;
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1600px;
            margin: 0 auto;
            padding: 20px;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 30px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        
        .header h1 {{
            font-size: 2.5rem;
            margin-bottom: 10px;
        }}
        
        .header-info {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}
        
        .info-item {{
            background: rgba(255,255,255,0.1);
            padding: 15px;
            border-radius: 8px;
        }}
        
        .info-label {{
            font-size: 0.9rem;
            opacity: 0.9;
            margin-bottom: 5px;
        }}
        
        .info-value {{
            font-size: 1.1rem;
            font-weight: 600;
            word-break: break-all;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
            transition: transform 0.3s;
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }}
        
        .stat-value {{
            font-size: 3rem;
            font-weight: 700;
            margin-bottom: 10px;
        }}
        
        .stat-label {{
            color: #7f8c8d;
            font-size: 1rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        
        .stat-card.passed .stat-value {{ color: #27ae60; }}
        .stat-card.failed .stat-value {{ color: #e74c3c; }}
        .stat-card.errors .stat-value {{ color: #f39c12; }}
        .stat-card.total .stat-value {{ color: #3498db; }}
        
        .progress-bar {{
            width: 100%;
            height: 30px;
            background: #ecf0f1;
            border-radius: 15px;
            overflow: hidden;
            margin-bottom: 30px;
            display: flex;
        }}
        
        .progress-passed {{
            background: #27ae60;
            height: 100%;
            transition: width 0.5s;
        }}
        
        .progress-failed {{
            background: #e74c3c;
            height: 100%;
            transition: width 0.5s;
        }}
        
        .progress-errors {{
            background: #f39c12;
            height: 100%;
            transition: width 0.5s;
        }}
        
        .controls {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
        }}
        
        .filter-group {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}
        
        .filter-btn {{
            padding: 10px 20px;
            border: 2px solid #ddd;
            background: white;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }}
        
        .filter-btn:hover {{
            border-color: #3498db;
            background: #ebf5fb;
        }}
        
        .filter-btn.active {{
            background: #3498db;
            color: white;
            border-color: #3498db;
        }}
        
        .search-box {{
            flex: 1;
            min-width: 250px;
        }}
        
        .search-box input {{
            width: 100%;
            padding: 10px 15px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
        }}
        
        .export-btn {{
            padding: 10px 20px;
            background: #27ae60;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.3s;
        }}
        
        .export-btn:hover {{
            background: #229954;
        }}
        
        .table-container {{
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        
        thead {{
            background: #34495e;
            color: white;
        }}
        
        th {{
            padding: 15px;
            text-align: left;
            font-weight: 600;
            cursor: pointer;
            user-select: none;
        }}
        
        th:hover {{
            background: #2c3e50;
        }}
        
        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #ecf0f1;
        }}
        
        tr:hover {{
            background: #f8f9fa;
        }}
        
        tr.failed {{
            background: #fee;
        }}
        
        tr.failed:hover {{
            background: #fdd;
        }}
        
        tr.error {{
            background: #fff3cd;
        }}
        
        tr.error:hover {{
            background: #ffeaa7;
        }}
        
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        
        .badge-200 {{
            background: #d4edda;
            color: #155724;
        }}
        
        .badge-404 {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .badge-redirect {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .badge-500 {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .badge-other {{
            background: #e2e3e5;
            color: #383d41;
        }}
        
        .path-cell {{
            max-width: 400px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .error-cell {{
            max-width: 300px;
            color: #e74c3c;
            font-size: 0.9rem;
        }}
        
        .btn-link {{
            display: inline-block;
            padding: 4px 8px;
            margin: 0 2px;
            background: #3498db;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            font-size: 0.85rem;
            transition: background 0.3s;
        }}
        
        .btn-link:hover {{
            background: #2980b9;
        }}
        
        .footer {{
            margin-top: 30px;
            text-align: center;
            color: #7f8c8d;
            padding: 20px;
        }}
        
        .select-all {{
            margin-right: 10px;
        }}
        
        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 1.8rem;
            }}
            
            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
            
            .table-container {{
                overflow-x: auto;
            }}
            
            table {{
                font-size: 0.9rem;
            }}
            
            th, td {{
                padding: 8px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 Sitemap Comparison Report</h1>
            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            
            <div class="header-info">
                <div class="info-item">
                    <div class="info-label">Baseline Sitemap</div>
                    <div class="info-value">{self.baseline_url}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">DEV Base URL</div>
                    <div class="info-value">{self.dev_url}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Total URLs Processed</div>
                    <div class="info-value">{self.stats.total_urls}</div>
                </div>
            </div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card total">
                <div class="stat-value">{self.stats.total_urls}</div>
                <div class="stat-label">Total URLs</div>
            </div>
            <div class="stat-card passed">
                <div class="stat-value">{self.stats.passed}</div>
                <div class="stat-label">Passed ({pass_rate:.1f}%)</div>
            </div>
            <div class="stat-card failed">
                <div class="stat-value">{self.stats.failed}</div>
                <div class="stat-label">Failed ({fail_rate:.1f}%)</div>
            </div>
            <div class="stat-card errors">
                <div class="stat-value">{self.stats.errors}</div>
                <div class="stat-label">Errors ({error_rate:.1f}%)</div>
            </div>
        </div>
        
        <div class="progress-bar">
            <div class="progress-passed" style="width: {pass_rate}%;" title="Passed: {self.stats.passed}"></div>
            <div class="progress-failed" style="width: {fail_rate}%;" title="Failed: {self.stats.failed}"></div>
            <div class="progress-errors" style="width: {error_rate}%;" title="Errors: {self.stats.errors}"></div>
        </div>
        
        <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));">
            <div class="stat-card">
                <div class="stat-value" style="color: #27ae60; font-size: 2rem;">{self.stats.status_200}</div>
                <div class="stat-label">200 OK</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #f39c12; font-size: 2rem;">{self.stats.status_301 + self.stats.status_302}</div>
                <div class="stat-label">Redirects</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #e74c3c; font-size: 2rem;">{self.stats.status_404}</div>
                <div class="stat-label">404 Errors</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #e74c3c; font-size: 2rem;">{self.stats.status_500}</div>
                <div class="stat-label">500 Errors</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #3498db; font-size: 2rem;">{self.stats.average_response_time:.2f}s</div>
                <div class="stat-label">Avg Response</div>
            </div>
        </div>
        
        <div class="controls">
            <div class="filter-group">
                <button class="filter-btn active" data-filter="all">All ({self.stats.total_urls})</button>
                <button class="filter-btn" data-filter="passed">✅ Passed ({self.stats.passed})</button>
                <button class="filter-btn" data-filter="failed">❌ Failed ({self.stats.failed})</button>
                <button class="filter-btn" data-filter="error">⚠️ Errors ({self.stats.errors})</button>
            </div>
            
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="🔍 Search URLs...">
            </div>
            
            <div>
                <label class="select-all">
                    <input type="checkbox" id="selectAll"> Select All
                </label>
            </div>
            
            <button class="export-btn" onclick="exportToCSV()">📊 Export to CSV</button>
        </div>
        
        <div class="table-container">
            <table id="resultsTable">
                <thead>
                    <tr>
                        <th width="30"><input type="checkbox" id="headerCheckbox"></th>
                        <th width="40"></th>
                        <th onclick="sortTable(2)">Path ⬍</th>
                        <th onclick="sortTable(3)" width="120">Status ⬍</th>
                        <th onclick="sortTable(4)" width="100">Response Time ⬍</th>
                        <th>Error/Redirect</th>
                        <th width="150">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>Sitemap Comparison Tool | Generated with ❤️ | {datetime.now().strftime('%Y')}</p>
        </div>
    </div>
    
    <script>
        // Filter functionality
        document.querySelectorAll('.filter-btn').forEach(btn => {{
            btn.addEventListener('click', function() {{
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                
                const filter = this.dataset.filter;
                const rows = document.querySelectorAll('#resultsTable tbody tr');
                
                rows.forEach(row => {{
                    if (filter === 'all') {{
                        row.style.display = '';
                    }} else if (filter === 'passed') {{
                        row.style.display = row.classList.contains('passed') ? '' : 'none';
                    }} else if (filter === 'failed') {{
                        row.style.display = row.classList.contains('failed') ? '' : 'none';
                    }} else if (filter === 'error') {{
                        row.style.display = row.classList.contains('error') ? '' : 'none';
                    }}
                }});
            }});
        }});
        
        // Search functionality
        document.getElementById('searchInput').addEventListener('keyup', function() {{
            const searchTerm = this.value.toLowerCase();
            const rows = document.querySelectorAll('#resultsTable tbody tr');
            
            rows.forEach(row => {{
                const path = row.querySelector('.path-cell').textContent.toLowerCase();
                const error = row.querySelector('.error-cell').textContent.toLowerCase();
                
                if (path.includes(searchTerm) || error.includes(searchTerm)) {{
                    row.style.display = '';
                }} else {{
                    row.style.display = 'none';
                }}
            }});
        }});
        
        // Select all functionality
        document.getElementById('selectAll').addEventListener('change', function() {{
            const checkboxes = document.querySelectorAll('.row-checkbox');
            checkboxes.forEach(cb => cb.checked = this.checked);
        }});
        
        document.getElementById('headerCheckbox').addEventListener('change', function() {{
            const checkboxes = document.querySelectorAll('.row-checkbox');
            checkboxes.forEach(cb => cb.checked = this.checked);
            document.getElementById('selectAll').checked = this.checked;
        }});
        
        // Sort table
        function sortTable(colIndex) {{
            const table = document.getElementById('resultsTable');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            const isAsc = table.dataset.sortOrder === 'asc';
            table.dataset.sortOrder = isAsc ? 'desc' : 'asc';
            
            rows.sort((a, b) => {{
                let aVal = a.children[colIndex].textContent.trim();
                let bVal = b.children[colIndex].textContent.trim();
                
                // Remove icons for comparison
                aVal = aVal.replace(/[✅❌⚠️]/g, '').trim();
                bVal = bVal.replace(/[✅❌⚠️]/g, '').trim();
                
                // Try numeric comparison for response time
                if (colIndex === 4) {{
                    aVal = parseFloat(aVal) || 0;
                    bVal = parseFloat(bVal) || 0;
                }}
                
                if (typeof aVal === 'number') {{
                    return isAsc ? aVal - bVal : bVal - aVal;
                }} else {{
                    return isAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                }}
            }});
            
            rows.forEach(row => tbody.appendChild(row));
        }}
        
        // Export to CSV
        function exportToCSV() {{
            const rows = [];
            const headers = ['Path', 'Status', 'Response Time', 'Error/Redirect', 'Baseline URL', 'DEV URL'];
            rows.push(headers.join(','));
            
            const visibleRows = Array.from(document.querySelectorAll('#resultsTable tbody tr'))
                .filter(row => row.style.display !== 'none');
            
            visibleRows.forEach(row => {{
                const path = row.querySelector('.path-cell').getAttribute('title') || row.querySelector('.path-cell').textContent;
                const status = row.querySelector('.badge').textContent;
                const time = row.children[4].textContent;
                const error = row.querySelector('.error-cell').textContent;
                const baselineLink = row.querySelector('a[href*="vehiclehistory"]').href;
                const devLink = row.querySelectorAll('a')[1].href;
                
                const rowData = [path, status, time, error, baselineLink, devLink];
                rows.push(rowData.map(cell => `"${{cell.replace(/"/g, '""')}}"`).join(','));
            }});
            
            const csvContent = rows.join('\\n');
            const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
            const link = document.createElement('a');
            const url = URL.createObjectURL(blob);
            
            link.setAttribute('href', url);
            link.setAttribute('download', 'sitemap_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv');
            link.style.visibility = 'hidden';
            
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }}
        
        // Keyboard shortcuts
        document.addEventListener('keydown', function(e) {{
            if (e.ctrlKey && e.key === 'a') {{
                e.preventDefault();
                document.getElementById('selectAll').checked = true;
                document.querySelectorAll('.row-checkbox').forEach(cb => cb.checked = true);
            }}
            if (e.ctrlKey && e.key === 'f') {{
                e.preventDefault();
                document.getElementById('searchInput').focus();
            }}
        }});
    </script>
</body>
</html>"""

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"\n{Fore.GREEN}✅ HTML report generated: {output_file}")

def save_json_data(results: List[URLResult], stats: SitemapStats, output_file: str):
    """Save results to JSON file"""
    data = {
        'timestamp': datetime.now().isoformat(),
        'stats': {
            'total_urls': stats.total_urls,
            'passed': stats.passed,
            'failed': stats.failed,
            'errors': stats.errors,
            'status_200': stats.status_200,
            'status_301': stats.status_301,
            'status_302': stats.status_302,
            'status_404': stats.status_404,
            'status_500': stats.status_500,
            'average_response_time': stats.average_response_time
        },
        'results': []
    }
    
    for result in results:
        data['results'].append({
            'baseline_url': result.baseline_url,
            'dev_url': result.dev_url,
            'path': result.path,
            'baseline_status': result.baseline_status,
            'dev_status': result.dev_status,
            'status': result.status,
            'response_time': result.response_time,
            'error_message': result.error_message,
            'page_title': result.page_title,
            'redirect_url': result.redirect_url,
            'exists_in_dev': result.exists_in_dev
        })
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"{Fore.GREEN}✅ JSON data saved: {output_file}")

def save_log(results: List[URLResult], stats: SitemapStats, output_file: str):
    """Save detailed log file"""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write("SITEMAP COMPARISON LOG\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*80 + "\n\n")
        
        f.write("SUMMARY\n")
        f.write("-"*40 + "\n")
        f.write(f"Total URLs: {stats.total_urls}\n")
        f.write(f"Passed: {stats.passed}\n")
        f.write(f"Failed: {stats.failed}\n")
        f.write(f"Errors: {stats.errors}\n")
        f.write(f"Average Response Time: {stats.average_response_time:.2f}s\n")
        f.write("\n" + "="*80 + "\n\n")
        
        f.write("DETAILED RESULTS\n")
        f.write("-"*40 + "\n\n")
        
        for i, result in enumerate(results, 1):
            f.write(f"[{i}/{stats.total_urls}] {result.path}\n")
            f.write(f"  Baseline URL: {result.baseline_url}\n")
            f.write(f"  DEV URL: {result.dev_url}\n")
            f.write(f"  Baseline Status: {result.baseline_status}\n")
            f.write(f"  DEV Status: {result.dev_status}\n")
            f.write(f"  Result: {result.status}\n")
            f.write(f"  Response Time: {result.response_time:.2f}s\n")
            if result.error_message:
                f.write(f"  Error: {result.error_message}\n")
            if result.page_title:
                f.write(f"  Page Title: {result.page_title}\n")
            f.write("\n")
    
    print(f"{Fore.GREEN}✅ Log file saved: {output_file}")

def main():
    """Main execution function"""
    print(f"\n{Back.BLUE}{Fore.WHITE}{'='*80}")
    print(f"{Back.BLUE}{Fore.WHITE} SITEMAP COMPARISON & URL VALIDATION TOOL ")
    print(f"{Back.BLUE}{Fore.WHITE}{'='*80}{Style.RESET_ALL}")
    
    print(f"\n{Fore.CYAN}Configuration:")
    print(f"{Fore.WHITE}Baseline Sitemap: {Fore.GREEN}{BASELINE_SITEMAP_URL}")
    print(f"{Fore.WHITE}DEV Base URL: {Fore.GREEN}{DEV_BASE_URL}")
    print(f"{Fore.WHITE}Output Directory: {Fore.GREEN}{OUTPUT_DIR}")
    
    start_time = time.time()
    
    # Step 1: Parse sitemap
    print(f"\n{Fore.YELLOW}{'='*60}")
    print(f"{Fore.YELLOW}STEP 1: Parsing Sitemap")
    print(f"{Fore.YELLOW}{'='*60}")
    
    parser = SitemapParser(BASELINE_SITEMAP_URL)
    urls = parser.parse_sitemap()
    
    if not urls:
        print(f"{Fore.RED}❌ No URLs found in sitemap. Exiting.")
        return
    
    # Remove duplicates while preserving order
    urls = list(dict.fromkeys(urls))
    print(f"\n{Fore.GREEN}📊 Total unique URLs: {len(urls)}")
    
    # Step 2: Compare URLs
    print(f"\n{Fore.YELLOW}{'='*60}")
    print(f"{Fore.YELLOW}STEP 2: Comparing URLs with DEV Environment")
    print(f"{Fore.YELLOW}{'='*60}")
    
    comparator = URLComparator(BASELINE_SITEMAP_URL, DEV_BASE_URL)
    results = comparator.compare_all_urls(urls, max_workers=10)
    
    # Step 3: Generate reports
    print(f"\n{Fore.YELLOW}{'='*60}")
    print(f"{Fore.YELLOW}STEP 3: Generating Reports")
    print(f"{Fore.YELLOW}{'='*60}")
    
    # HTML Report
    report_generator = HTMLReportGenerator(
        results, comparator.stats, BASELINE_SITEMAP_URL, DEV_BASE_URL
    )
    report_generator.generate(REPORT_FILE)
    
    # JSON Data
    save_json_data(results, comparator.stats, JSON_FILE)
    
    # Log file
    save_log(results, comparator.stats, LOG_FILE)
    
    total_time = time.time() - start_time
    
    # Final summary
    print(f"\n{Back.GREEN}{Fore.BLACK}{'='*80}")
    print(f"{Back.GREEN}{Fore.BLACK} EXECUTION COMPLETE ")
    print(f"{Back.GREEN}{Fore.BLACK}{'='*80}{Style.RESET_ALL}")
    
    print(f"\n{Fore.CYAN}📊 Final Statistics:")
    print(f"{Fore.WHITE}  Total URLs: {comparator.stats.total_urls}")
    print(f"{Fore.GREEN}  ✅ Passed: {comparator.stats.passed} ({comparator.stats.passed/comparator.stats.total_urls*100:.1f}%)")
    print(f"{Fore.RED}  ❌ Failed (404): {comparator.stats.failed}")
    print(f"{Fore.YELLOW}  ⚠️ Errors: {comparator.stats.errors}")
    print(f"{Fore.CYAN}  ⏱️ Total Time: {total_time:.2f}s")
    print(f"{Fore.CYAN}  📊 Avg Response: {comparator.stats.average_response_time:.2f}s")
    
    print(f"\n{Fore.GREEN}📁 Output Files:")
    print(f"  📄 HTML Report: {os.path.abspath(REPORT_FILE)}")
    print(f"  📊 JSON Data: {os.path.abspath(JSON_FILE)}")
    print(f"  📝 Log File: {os.path.abspath(LOG_FILE)}")
    
    # Open HTML report in browser
    print(f"\n{Fore.CYAN}🌐 Opening report in browser...")
    webbrowser.open(f"file://{os.path.abspath(REPORT_FILE)}")
    
    print(f"\n{Fore.GREEN}{'='*80}")
    print(f"{Fore.GREEN}✅ Done! Check the report for detailed results.")
    print(f"{Fore.GREEN}{'='*80}{Style.RESET_ALL}")

if __name__ == "__main__":
    main()