import random
import logging
from django.utils import timezone
from asgiref.sync import sync_to_async
from django.db.models import Sum

from core.models import (
    DailyObjectiveTemplate, 
    UserDailyObjective, 
    WeeklyObjectiveTemplate,
    UserWeeklyObjective,
    ObjectiveRewardPoolItem,
    WeeklyObjectiveRewardPoolItem,
    UserCard
)

log = logging.getLogger("matchdex.objectives")

def _sync_get_or_assign_daily_objectives(user):
    today = timezone.now().date()
    
    # Get current objectives for today
    objectives = list(UserDailyObjective.objects.filter(user=user, date=today).select_related('template'))
    
    if len(objectives) < 3:
        # User needs new objectives (or missing some)
        existing_templates = [obj.template.id for obj in objectives]
        
        # Get templates from yesterday to avoid repeats if possible
        yesterday = today - timezone.timedelta(days=1)
        yesterday_templates = list(UserDailyObjective.objects.filter(user=user, date=yesterday).values_list('template_id', flat=True))
        
        # Get all templates
        available_templates = list(DailyObjectiveTemplate.objects.exclude(id__in=existing_templates))
        
        if not available_templates:
            return objectives # No templates defined in DB
            
        fresh_templates = [t for t in available_templates if t.id not in yesterday_templates]
        needed = 3 - len(objectives)
        
        if len(fresh_templates) >= needed:
            selected_templates = random.sample(fresh_templates, needed)
        else:
            selected_templates = list(fresh_templates)
            remaining_needed = needed - len(fresh_templates)
            fallback_templates = [t for t in available_templates if t.id in yesterday_templates]
            if fallback_templates:
                selected_templates.extend(random.sample(fallback_templates, min(remaining_needed, len(fallback_templates))))
        
        new_objectives = []
        for template in selected_templates:
            new_obj = UserDailyObjective(user=user, template=template, date=today)
            new_obj.save()
            new_objectives.append(new_obj)
            
        objectives.extend(new_objectives)
        
    return objectives

@sync_to_async
def get_or_assign_daily_objectives(user):
    return _sync_get_or_assign_daily_objectives(user)


def _sync_get_or_assign_weekly_objectives(user):
    today = timezone.now().date()
    # Monday of the current week
    week_start = today - timezone.timedelta(days=today.weekday())
    
    objectives = list(UserWeeklyObjective.objects.filter(user=user, week_start=week_start).select_related('template'))
    
    if len(objectives) < 3:
        existing_templates = [obj.template.id for obj in objectives]
        prev_week = week_start - timezone.timedelta(days=7)
        prev_templates = list(UserWeeklyObjective.objects.filter(user=user, week_start=prev_week).values_list('template_id', flat=True))
        
        available_templates = list(WeeklyObjectiveTemplate.objects.exclude(id__in=existing_templates))
        if not available_templates:
            return objectives
            
        fresh_templates = [t for t in available_templates if t.id not in prev_templates]
        needed = 3 - len(objectives)
        
        if len(fresh_templates) >= needed:
            selected_templates = random.sample(fresh_templates, needed)
        else:
            selected_templates = list(fresh_templates)
            remaining_needed = needed - len(fresh_templates)
            fallback_templates = [t for t in available_templates if t.id in prev_templates]
            if fallback_templates:
                selected_templates.extend(random.sample(fallback_templates, min(remaining_needed, len(fallback_templates))))
                
        new_objectives = []
        for template in selected_templates:
            new_obj = UserWeeklyObjective(user=user, template=template, week_start=week_start)
            new_obj.save()
            new_objectives.append(new_obj)
            
        objectives.extend(new_objectives)
        
    return objectives

@sync_to_async
def get_or_assign_weekly_objectives(user):
    return _sync_get_or_assign_weekly_objectives(user)


def _sync_update_objective_progress(user, objective_type, amount=1):
    """
    Updates progress for both daily and weekly objectives simultaneously.
    """
    _sync_get_or_assign_daily_objectives(user)
    _sync_get_or_assign_weekly_objectives(user)
    
    today = timezone.now().date()
    week_start = today - timezone.timedelta(days=today.weekday())
    
    # 1. Update active daily objectives
    daily_objs = UserDailyObjective.objects.filter(
        user=user, 
        date=today, 
        template__objective_type=objective_type,
        is_claimed=False
    ).select_related('template')
    
    for obj in daily_objs:
        if obj.progress < obj.template.target_amount:
            obj.progress = min(obj.progress + amount, obj.template.target_amount)
            obj.save()

    # 2. Update active weekly objectives
    weekly_objs = UserWeeklyObjective.objects.filter(
        user=user,
        week_start=week_start,
        template__objective_type=objective_type,
        is_claimed=False
    ).select_related('template')
    
    for obj in weekly_objs:
        if obj.progress < obj.template.target_amount:
            obj.progress = min(obj.progress + amount, obj.template.target_amount)
            obj.save()

@sync_to_async
def update_objective_progress(user, objective_type, amount=1):
    return _sync_update_objective_progress(user, objective_type, amount)


@sync_to_async
def claim_objective_reward(user, objective_id):
    """
    Claims the reward for a completed daily objective.
    Returns a string describing the reward, or None if invalid.
    """
    try:
        obj = UserDailyObjective.objects.get(id=objective_id, user=user)
    except UserDailyObjective.DoesNotExist:
        return None
        
    if obj.is_claimed or obj.progress < obj.template.target_amount:
        return None
        
    pool_items = list(ObjectiveRewardPoolItem.objects.all())
    if not pool_items:
        return "No rewards configured in the prize pool."
        
    weights = [item.weight for item in pool_items]
    reward = random.choices(pool_items, weights=weights, k=1)[0]
    
    reward_text = ""
    if reward.reward_type == "POINTS":
        user.points += reward.amount
        user.save()
        reward_text = f"**{reward.amount} Points**"
    elif reward.reward_type == "PACK":
        if reward.pack:
            from core.models import UserPack
            up, _ = UserPack.objects.get_or_create(user=user, pack=reward.pack)
            up.stash_count += reward.amount
            up.save()
            reward_text = f"**{reward.amount}x {reward.pack.name} Pack**"
        else:
            reward_text = "Invalid Pack Configuration"
    elif reward.reward_type == "CARD":
        if reward.card:
            new_card = UserCard.objects.create(
                owner=user,
                template=reward.card
            )
            reward_text = f"**{reward.card.name} ({reward.card.rarity})**"
        else:
            reward_text = "Invalid Card Configuration"
            
    obj.is_claimed = True
    obj.save()
    
    return reward_text


@sync_to_async
def claim_weekly_objective_reward(user, objective_id):
    """
    Claims the reward for a completed weekly objective.
    Grants template-specific reward or weekly pool items.
    """
    try:
        obj = UserWeeklyObjective.objects.select_related('template', 'template__reward_pack', 'template__reward_card').get(id=objective_id, user=user)
    except UserWeeklyObjective.DoesNotExist:
        return None

    if obj.is_claimed or obj.progress < obj.template.target_amount:
        return None

    template = obj.template
    reward_text = ""

    # 1. Check if template has a specific reward configured
    if template.reward_type == "POINTS":
        pts = template.reward_amount
        user.points += pts
        user.save()
        reward_text = f"**{pts} Points**"
    elif template.reward_type == "PACK":
        if template.reward_pack:
            from core.models import UserPack
            up, _ = UserPack.objects.get_or_create(user=user, pack=template.reward_pack)
            count = max(template.reward_amount, 1)
            up.stash_count += count
            up.save()
            reward_text = f"**{count}x {template.reward_pack.name} Pack**"
        else:
            user.points += 500
            user.save()
            reward_text = "**500 Bonus Points**"
    elif template.reward_type == "CARD":
        if template.reward_card:
            UserCard.objects.create(owner=user, template=template.reward_card)
            reward_text = f"**{template.reward_card.name} ({template.reward_card.rarity})**"
        else:
            user.points += 500
            user.save()
            reward_text = "**500 Bonus Points**"
    else:
        # 2. Draw from Weekly Reward Pool
        weekly_pool = list(WeeklyObjectiveRewardPoolItem.objects.select_related('pack', 'card').all())
        if weekly_pool:
            weights = [item.weight for item in weekly_pool]
            reward = random.choices(weekly_pool, weights=weights, k=1)[0]
            if reward.reward_type == "POINTS":
                user.points += reward.amount
                user.save()
                reward_text = f"**{reward.amount} Points**"
            elif reward.reward_type == "PACK":
                if reward.pack:
                    from core.models import UserPack
                    up, _ = UserPack.objects.get_or_create(user=user, pack=reward.pack)
                    up.stash_count += max(reward.amount, 1)
                    up.save()
                    reward_text = f"**{max(reward.amount, 1)}x {reward.pack.name} Pack**"
                else:
                    user.points += 500
                    user.save()
                    reward_text = "**500 Bonus Points**"
            elif reward.reward_type == "CARD":
                if reward.card:
                    UserCard.objects.create(owner=user, template=reward.card)
                    reward_text = f"**{reward.card.name} ({reward.card.rarity})**"
                else:
                    user.points += 500
                    user.save()
                    reward_text = "**500 Bonus Points**"
        else:
            # Fallback to standard pool with 2x bonus or default 500 points
            pool_items = list(ObjectiveRewardPoolItem.objects.select_related('pack', 'card').all())
            if pool_items:
                weights = [item.weight for item in pool_items]
                reward = random.choices(pool_items, weights=weights, k=1)[0]
                if reward.reward_type == "POINTS":
                    pts = reward.amount * 2
                    user.points += pts
                    user.save()
                    reward_text = f"**{pts} Points**"
                elif reward.reward_type == "PACK":
                    if reward.pack:
                        from core.models import UserPack
                        up, _ = UserPack.objects.get_or_create(user=user, pack=reward.pack)
                        packs = max(reward.amount, 2)
                        up.stash_count += packs
                        up.save()
                        reward_text = f"**{packs}x {reward.pack.name} Pack**"
                    else:
                        user.points += 500
                        user.save()
                        reward_text = "**500 Bonus Points**"
                elif reward.reward_type == "CARD":
                    if reward.card:
                        UserCard.objects.create(owner=user, template=reward.card)
                        reward_text = f"**{reward.card.name} ({reward.card.rarity})**"
                    else:
                        user.points += 500
                        user.save()
                        reward_text = "**500 Bonus Points**"
            else:
                user.points += 500
                user.save()
                reward_text = "**500 Bonus Points**"

    obj.is_claimed = True
    obj.save()
    return reward_text
