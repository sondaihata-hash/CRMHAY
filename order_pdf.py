import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF


def build_order_pdf(order, organization=None):
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4)
    font = 'Helvetica'
    try:
        pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        font = 'DejaVu'
    except Exception:
        pass
    width, height = A4
    pdf.setStrokeColorRGB(.25, .25, .25)
    pdf.rect(10*mm, 10*mm, width-20*mm, height-20*mm)
    pdf.setFont(font, 8)
    company_name = getattr(organization, 'name', None) or 'CRM HAY'
    company_address = getattr(organization, 'company_address', None) or 'Chưa cập nhật'
    company_phone = getattr(organization, 'company_phone', None) or '-'
    company_email = getattr(organization, 'company_email', None) or '-'
    pdf.drawString(14*mm, height-16*mm, company_name)
    pdf.drawString(14*mm, height-21*mm, f'Địa chỉ: {company_address}')
    pdf.drawString(14*mm, height-26*mm, f'Điện thoại: {company_phone} | Email: {company_email}')
    pdf.setFont(font, 14); pdf.drawCentredString(width/2, height-34*mm, 'ĐƠN ĐẶT HÀNG')
    pdf.setFont(font, 9)
    pdf.drawString(14*mm, height-44*mm, f'Tên khách hàng: {order.customer.name}')
    pdf.drawString(14*mm, height-50*mm, f'Địa chỉ: {order.delivery_address or "-"}')
    pdf.drawString(14*mm, height-56*mm, f'Điện thoại: {order.customer.phone or "-"}')
    pdf.drawString(14*mm, height-61*mm, f'SĐT Sales: {order.sales_phone or "-"}')
    pdf.drawString(120*mm, height-56*mm, f'STK Sales: {order.sales_bank_account or "-"}')
    pdf.drawString(120*mm, height-44*mm, f'Ngày: {order.created_at:%d/%m/%Y}')
    pdf.drawString(120*mm, height-50*mm, f'Số: {order.code}')
    y = height-65*mm
    columns = [14, 25, 95, 115, 130, 160, 196]
    headers = ['STT', 'Tên hàng', 'ĐVT', 'SL', 'Đơn giá', 'Thành tiền']
    pdf.line(14*mm, y, 196*mm, y)
    for i, header in enumerate(headers): pdf.drawString(columns[i]*mm, y-5*mm, header)
    y -= 9*mm; pdf.line(14*mm, y, 196*mm, y)
    for index, item in enumerate(order.items, 1):
        if y < 55*mm: break
        values = [str(index), item.product_name[:35], item.unit or '', f'{item.quantity:g}', f'{item.unit_price:,.0f}', f'{item.quantity*item.unit_price:,.0f}']
        for i, value in enumerate(values): pdf.drawString(columns[i]*mm, y-5*mm, value)
        y -= 8*mm; pdf.line(14*mm, y, 196*mm, y)
    pdf.setFont(font, 10)
    pdf.drawRightString(196*mm, y-8*mm, f'Chiết khấu: {order.discount_amount:,.0f} đ')
    pdf.drawRightString(196*mm, y-14*mm, f'Đổi điểm: {order.points_redeemed or 0} điểm x {(order.points_value or 1000):,.0f} đ (-{(order.points_discount or 0):,.0f} đ)')
    pdf.drawRightString(196*mm, y-20*mm, f'VAT: {order.vat_amount:,.0f} đ')
    pdf.setFont(font, 11); pdf.drawRightString(196*mm, y-27*mm, f'TỔNG THANH TOÁN: {order.total_amount:,.0f} đ')
    pdf.setFont(font, 9); pdf.drawString(14*mm, 40*mm, 'Người lập'); pdf.drawCentredString(width/2, 40*mm, 'Kế toán trưởng'); pdf.drawRightString(196*mm, 40*mm, 'Khách hàng')
    payment = getattr(order, 'payment', None)
    if payment and payment.status != 'paid' and payment.qr_code:
        qr_widget = qr.QrCodeWidget(payment.qr_code)
        bounds = qr_widget.getBounds()
        drawing = Drawing(32*mm, 32*mm, transform=[32*mm/(bounds[2] - bounds[0]), 0, 0, 32*mm/(bounds[3] - bounds[1]), 0, 0])
        drawing.add(qr_widget)
        renderPDF.draw(drawing, pdf, 158*mm, 43*mm)
        pdf.setFont(font, 7)
        pdf.drawCentredString(174*mm, 40*mm, 'Quét để thanh toán')
    pdf.save(); stream.seek(0); return stream
