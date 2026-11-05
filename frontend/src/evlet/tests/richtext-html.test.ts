// The browser's HTML reading and writing (richtext/html.ts) against the server's sanitiser cases, paste
// fixtures from Word, Outlook, Google Docs and web pages, and plain text.
import { describe, expect, it } from 'vitest';
import { canonical, linkFromInput, parseHTML, parsePastedHTML, parsePlainText, safeHref, toHTML } from '../richtext/html';
import { plainText } from '../richtext/model';
import cases from './richtext-cases.json';

describe('shared cases with evlet/richtext.py', () => {
  for (const [source, expected, text] of cases.cases as Array<[string, string, string]>) {
    it(source.slice(0, 60), () => {
      const doc = parseHTML(source);
      expect(toHTML(doc)).toBe(expected);
      expect(plainText(doc)).toBe(text);
      expect(canonical(expected)).toBe(expected);
    });
  }
});

const WORD = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word">
<head><meta name=Generator content="Microsoft Word 15"><style><!-- p.MsoNormal {margin:0cm; font-family:"Calibri",sans-serif;} --></style></head>
<body lang=EN-US style='tab-interval:36.0pt'><!--StartFragment-->
<p class=MsoNormal><b><span style='font-size:14.0pt'>Quarterly review<o:p></o:p></span></b></p>
<p class=MsoNormal>Revenue grew <i>12%</i> in <span style='color:red'>Dubai</span>.<o:p></o:p></p>
<p class=MsoListParagraphCxSpFirst style='text-indent:-18.0pt;mso-list:l0 level1 lfo1'><![if !supportLists]><span style='font-family:Symbol;mso-list:Ignore'>·<span style='font:7.0pt "Times New Roman"'>&nbsp;&nbsp;&nbsp; </span></span><![endif]>Hire two engineers<o:p></o:p></p>
<p class=MsoListParagraphCxSpLast style='margin-left:72.0pt;text-indent:-18.0pt;mso-list:l0 level2 lfo1'><![if !supportLists]><span style='font-family:"Courier New";mso-list:Ignore'>o<span style='font:7.0pt "Times New Roman"'>&nbsp;&nbsp; </span></span><![endif]>Backend first<o:p></o:p></p>
<p class=MsoListParagraph style='mso-list:l1 level1 lfo2'><![if !supportLists]><span style='mso-list:Ignore'>1.<span>&nbsp;&nbsp;</span></span><![endif]>Ship it<o:p></o:p></p>
<script>alert(1)</script><!--EndFragment--></body></html>`;

const GOOGLE_DOCS = `<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1a2b3c"><h1 dir="ltr" style="line-height:1.38;"><span style="font-size:20pt;font-weight:400;">Launch plan</span></h1>
<p dir="ltr" style="line-height:1.38;"><span style="font-weight:700;">Owner:</span><span style="font-weight:400;"> Priya </span><span style="font-style:italic;">(draft)</span></p>
<ul style="margin-top:0;"><li dir="ltr" aria-level="1" style="list-style-type:disc;"><p dir="ltr" role="presentation"><span>Book venue</span></p></li>
<li dir="ltr" aria-level="2" style="list-style-type:circle;"><p dir="ltr" role="presentation"><span style="text-decoration:underline;">Al Quoz</span></p></li></ul>
<p dir="ltr"><a href="https://docs.example.com/x" style="text-decoration:none;"><span style="color:#1155cc;text-decoration:underline;">Brief</span></a></p></b><br class="Apple-interchange-newline">`;

const OUTLOOK = `<html><body><div class="WordSection1"><p class="MsoNormal">Hi team,<o:p></o:p></p><p class="MsoNormal"><o:p>&nbsp;</o:p></p>
<p class="MsoNormal">Please review <a href="https://intranet.example.ae/doc?id=4&amp;v=2">the draft</a> by <b>Thursday</b>.<o:p></o:p></p>
<p class="MsoNormal"><span style="color:#1F497D">Thanks,<br>Omar</span></p><img src="cid:image001.png@01DA"></div></body></html>`;

const WEB = `<div class="post" style="font-family:Arial"><h4 class="title" id="x" onclick="track()">Release notes</h4><p>Version <strong>2.1</strong>
is out. <a href="javascript:steal()" target="_blank">Click</a> or <a href="https://evlet.io/notes" rel="noopener">read more</a>.</p>
<iframe src="https://ads.example"></iframe><table><tr><td>Fixed</td><td>12 bugs</td></tr></table><pre class="code">npm   test</pre>
<blockquote cite="x"><p>Fast</p></blockquote><svg><text>vector</text></svg><img src="https://x.io/a.png" alt="photo"></div>`;

describe('paste', () => {
  it('cleans Word HTML, with its lists', () => {
    expect(toHTML({ blocks: parsePastedHTML(WORD) })).toBe('<p><strong>Quarterly review</strong></p><p>Revenue grew <em>12%</em> in Dubai.</p>'
      + '<ul><li>Hire two engineers<ul><li>Backend first</li></ul></li></ul><ol><li>Ship it</li></ol>');
  });
  it('cleans Google Docs HTML: not all bold, styles become marks, aria-level nests', () => {
    expect(toHTML({ blocks: parsePastedHTML(GOOGLE_DOCS) })).toBe('<h2>Launch plan</h2><p><strong>Owner:</strong> Priya <em>(draft)</em></p>'
      + '<ul><li>Book venue<ul><li><u>Al Quoz</u></li></ul></li></ul><p><a href="https://docs.example.com/x"><u>Brief</u></a></p>');
  });
  it('cleans Outlook HTML', () => {
    expect(toHTML({ blocks: parsePastedHTML(OUTLOOK) })).toBe('<p>Hi team,</p><p></p><p>Please review <a href="https://intranet.example.ae/doc?id=4&amp;v=2">the draft</a> by <strong>Thursday</strong>.</p>'
      + '<p>Thanks,<br>Omar</p>');
  });
  it('cleans web pages: no scripts, frames, styles, classes, SVG or unsafe links', () => {
    const stored = toHTML({ blocks: parsePastedHTML(WEB) });
    expect(stored).toBe('<h3>Release notes</h3><p>Version <strong>2.1</strong> is out. Click or <a href="https://evlet.io/notes">read more</a>.</p>'
      + '<p>Fixed</p><p>12 bugs</p><pre><code>npm   test</code></pre><blockquote><p>Fast</p></blockquote>');
    expect(stored).not.toMatch(/class=|style=|onclick|javascript|iframe|svg|img/);
  });
  it('reads plain text: paragraphs, lines and links', () => {
    expect(toHTML({ blocks: parsePlainText('First line\nsecond line\n\nNew para see https://evlet.io/docs. and www.example.com\r\n') }))
      .toBe('<p>First line<br>second line</p><p>New para see <a href="https://evlet.io/docs">https://evlet.io/docs</a>. and <a href="https://www.example.com">www.example.com</a></p>');
  });
});

describe('links', () => {
  it('keeps http, https and mailto only', () => {
    expect(safeHref('https://evlet.io')).toBe('https://evlet.io');
    expect(safeHref(' java\tscript:alert(1)')).toBeNull();
    expect(safeHref('data:text/html,x')).toBeNull();
    expect(safeHref('//evil.io')).toBeNull();
    expect(safeHref('https://x.io:99999')).toBeNull();
  });
  it('reads what a person types as a link', () => {
    expect(linkFromInput('evlet.io/docs')).toBe('https://evlet.io/docs');
    expect(linkFromInput('me@evlet.io')).toBe('mailto:me@evlet.io');
    expect(linkFromInput('javascript:alert(1)')).toBeNull();
    expect(linkFromInput('  ')).toBeNull();
  });
});

describe('shared mention cases with evlet/richtext.py (the activity journal)', () => {
  for (const [source, expected, text] of (cases as any).mention_cases as Array<[string, string, string]>) {
    it(source.slice(0, 60), () => {
      const doc = parseHTML(source, { mentions: true });
      expect(toHTML(doc)).toBe(expected);
      expect(plainText(doc)).toBe(text);
      expect(toHTML(parseHTML(expected, { mentions: true }))).toBe(expected);
    });
  }
});
