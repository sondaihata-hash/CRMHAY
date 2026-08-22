import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def build_order_pdf(order):
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
    pdf.drawString(14*mm, height-16*mm, 'CÔNG TY CỔ PHẦN SƠN IHATA VIỆT NAM')
    pdf.drawString(14*mm, height-21*mm, 'Địa chỉ: 4 Phan Huy Ích, Phường 15, Gò Vấp, TP. Hồ Chí Minh')
    pdf.setFont(font, 14); pdf.drawCentredString(width/2, height-34*mm, 'ĐƠN ĐẶT HÀNG')
    pdf.setFont(font, 9)
    pdf.drawString(14*mm, height-44*mm, f'Tên khách hàng: {order.customer.name}')
    pdf.drawString(14*mm, height-50*mm, f'Địa chỉ: {order.delivery_address or "-"}')
    pdf.drawString(14*mm, height-56*mm, f'Điện thoại: {order.customer.phone or "-"}')
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
    pdf.drawRightString(196*mm, y-14*mm, f'VAT: {order.vat_amount:,.0f} đ')
    pdf.setFont(font, 11); pdf.drawRightString(196*mm, y-21*mm, f'TỔNG THANH TOÁN: {order.total_amount:,.0f} đ')
    pdf.setFont(font, 9); pdf.drawString(14*mm, 40*mm, 'Người lập'); pdf.drawCentredString(width/2, 40*mm, 'Kế toán trưởng'); pdf.drawRightString(196*mm, 40*mm, 'Khách hàng')
    pdf.save(); stream.seek(0); return stream
