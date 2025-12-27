async def show_content_details(update: Update, context: ContextTypes.DEFAULT_TYPE, content_id):
    """عرض تفاصيل محتوى محدد (مسلسل أو فيلم)"""
    query = update.callback_query
    
    # جلب معلومات المحتوى
    content_info = await get_content_info(content_id)
    if not content_info:
        await query.edit_message_text("❌ المحتوى غير موجود.")
        return
    
    content_id, name, content_type = content_info
    episodes = await get_content_episodes(content_id)
    
    type_arabic = "مسلسل" if content_type == 'series' else "فيلم"
    type_icon = "📺" if content_type == 'series' else "🎬"
    
    if not episodes:
        message_text = f"{type_icon} *{name}*\n\n📭 لا توجد { 'حلقات' if content_type == 'series' else 'أجزاء' } حالياً."
        keyboard = [[InlineKeyboardButton("⬅️ رجوع", callback_data=f"{content_type}_list")]]
        await query.edit_message_text(
            message_text, 
            parse_mode='Markdown', 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # تجميع الحلقات حسب الموسم
    seasons = {}
    for ep in episodes:
        ep_id, season, ep_num, msg_id, channel_id = ep
        if season not in seasons:
            seasons[season] = []
        seasons[season].append((ep_id, ep_num, msg_id, channel_id))
    
    # إذا كان المحتوى من نوع مسلسل وكان له أكثر من موسم، نعرض قائمة المواسم
    if content_type == 'series' and len(seasons) > 1:
        message_text = f"{type_icon} *{name}*\n\nاختر الموسم:"
        keyboard = []
        for season_num in sorted(seasons.keys()):
            # حساب عدد الحلقات في هذا الموسم
            ep_count = len(seasons[season_num])
            keyboard.append([
                InlineKeyboardButton(
                    f"📁 الموسم {season_num} ({ep_count} حلقة)",
                    callback_data=f"season_{content_id}_{season_num}"
                )
            ])
    else:
        # إذا كان فيلم أو مسلسل له موسم واحد فقط، نعرض الحلقات مباشرة
        # للأفلام، نعرض الأجزاء (الموسم هنا يمثل الجزء)
        if content_type == 'movie':
            message_text = f"{type_icon} *{name}*\n\nاختر الجزء:"
        else:
            # للمسلسلات ذات الموسم الواحد
            season_num = list(seasons.keys())[0] if seasons else 1
            message_text = f"{type_icon} *{name}*\n\nاختر الحلقة:"
        
        keyboard = []
        # نستخدم أول موسم (إذا كان مسلسل) أو كل الحلقات مجمعة في موسم واحد
        season_num = list(seasons.keys())[0] if seasons else 1
        season_episodes = seasons.get(season_num, [])
        
        # تقسيم أزرار الحلقات (5 أزرار في كل صف)
        row_buttons = []
        for ep_id, ep_num, msg_id, channel_id in season_episodes:
            if content_type == 'movie':
                button_text = f"🎬 الجزء {ep_num}"
            else:
                button_text = f"الحلقة {ep_num}"
            
            row_buttons.append(
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"ep_{ep_id}"
                )
            )
            
            # كل 5 أزرار نبدأ صف جديد
            if len(row_buttons) == 5:
                keyboard.append(row_buttons)
                row_buttons = []
        
        if row_buttons:
            keyboard.append(row_buttons)
    
    # أزرار التنقل
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع", callback_data=f"{content_type}_list"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_season_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, content_id, season_num):
    """عرض حلقات موسم محدد لمسلسل"""
    query = update.callback_query
    
    # جلب معلومات المحتوى
    content_info = await get_content_info(content_id)
    if not content_info:
        await query.edit_message_text("❌ المحتوى غير موجود.")
        return
    
    content_id, name, content_type = content_info
    episodes = await get_content_episodes(content_id)
    
    if not episodes:
        await query.edit_message_text("❌ لا توجد حلقات لهذا الموسم.")
        return
    
    # تصفية الحلقات للموسم المحدد
    season_episodes = [ep for ep in episodes if ep[1] == season_num]
    
    if not season_episodes:
        await query.edit_message_text(f"❌ لا توجد حلقات للموسم {season_num}.")
        return
    
    message_text = f"📺 *{name}*\n📁 الموسم {season_num}\n\nاختر الحلقة:"
    
    keyboard = []
    # تقسيم أزرار الحلقات (5 أزرار في كل صف)
    row_buttons = []
    for ep in season_episodes:
        ep_id, season, ep_num, msg_id, channel_id = ep
        row_buttons.append(
            InlineKeyboardButton(
                f"الحلقة {ep_num}",
                callback_data=f"ep_{ep_id}"
            )
        )
        if len(row_buttons) == 5:
            keyboard.append(row_buttons)
            row_buttons = []
    
    if row_buttons:
        keyboard.append(row_buttons)
    
    # أزرار التنقل
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع للمسلسل", callback_data=f"content_{content_id}"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# تحديث معالج الأزرار لإضافة دعم للمواسم
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار InlineKeyboard"""
    query = update.callback_query
    await query.answer()  # مهم لإعلام تليجرام
    
    data = query.data
    
    if data == 'home':
        await start(update, context)
        return
    
    elif data == 'test_db':
        await test_db_button(update, context)
        return
    
    elif data == 'all_content':
        await show_content(update, context)
        return
    
    elif data == 'series_list':
        await show_content(update, context, 'series')
        return
    
    elif data == 'movies_list':
        await show_content(update, context, 'movie')
        return
    
    elif data.startswith('content_'):
        content_id = int(data.split('_')[1])
        await show_content_details(update, context, content_id)
        return
    
    elif data.startswith('ep_'):
        episode_id = int(data.split('_')[1])
        await show_episode_details(update, context, episode_id)
        return
    
    elif data.startswith('season_'):
        # بيانات الزر: season_<content_id>_<season_number>
        parts = data.split('_')
        content_id = int(parts[1])
        season_num = int(parts[2])
        await show_season_episodes(update, context, content_id, season_num)
        return
